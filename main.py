from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import sqlite3
import secrets
import httpx
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os
import uvicorn
import json

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB = "database.db"
SESSION_FILE = "session.json"

# ================= DATABASE =================

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            api_key TEXT,
            max_devices INTEGER,
            expires_at TEXT,
            created_at TEXT,
            usage_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()

init_db()

# ================= SESSION =================

def save_session(cookies, csrf_token):
    data = {
        "cookies": cookies,
        "csrf_token": csrf_token
    }
    with open(SESSION_FILE, "w") as f:
        json.dump(data, f)

def load_session():
    if not os.path.exists(SESSION_FILE):
        return None
    with open(SESSION_FILE, "r") as f:
        return json.load(f)

# ================= LOGIN =================

async def perform_login(client, username, password):
    LOGIN_URL = "https://ivas.tempnum.qzz.io/login"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://ivas.tempnum.qzz.io",
        "Referer": LOGIN_URL
    }

    login_page = await client.get(LOGIN_URL, headers=headers)
    soup = BeautifulSoup(login_page.text, "html.parser")
    token_input = soup.find("input", {"name": "_token"})

    login_data = {
        "email": username,
        "password": password
    }

    if token_input:
        login_data["_token"] = token_input["value"]

    login_res = await client.post(LOGIN_URL, data=login_data, headers=headers)

    if "dashboard" not in str(login_res.url) and "portal" not in str(login_res.url):
        return None, None

    cookies = dict(client.cookies)
    csrf_token = cookies.get("XSRF-TOKEN")

    save_session(cookies, csrf_token)

    return cookies, csrf_token

# ================= FETCH SMS =================

async def fetch_sms(client, csrf_token):
    BASE_URL = "https://ivas.tempnum.qzz.io"
    SMS_API = urljoin(BASE_URL, "portal/sms/received/getsms")

    today = datetime.utcnow()
    yesterday = today - timedelta(days=1)

    payload = {
        "from": yesterday.strftime("%m/%d/%Y"),
        "to": today.strftime("%m/%d/%Y"),
        "_token": csrf_token
    }

    headers = {"User-Agent": "Mozilla/5.0"}

    res = await client.post(SMS_API, data=payload, headers=headers)

    if "login" in res.text.lower():
        return None

    soup = BeautifulSoup(res.text, "html.parser")
    cards = soup.find_all("div", class_="card-body")

    messages = []

    for card in cards:
        p = card.find("p")
        if not p:
            continue

        text = p.get_text(strip=True)
        code_match = re.search(r"\b(\d{4,8})\b", text)
        code = code_match.group(1) if code_match else "N/A"

        messages.append({
            "code": code,
            "full_sms": text,
            "time": str(datetime.utcnow())
        })

    return messages

# ================= ADMIN =================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db()
    keys = conn.execute("SELECT * FROM api_keys").fetchall()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "keys": keys
    })

@app.post("/admin/create")
async def create_key(
    name: str = Form(...),
    max_devices: int = Form(...),
    expires_at: str = Form(...)
):
    key = "sk_" + secrets.token_hex(32)

    conn = get_db()
    conn.execute("""
        INSERT INTO api_keys 
        (name, api_key, max_devices, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (name, key, max_devices, expires_at, str(datetime.now())))
    conn.commit()

    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete")
async def delete_key(key_id: int = Form(...)):
    conn = get_db()
    conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    return RedirectResponse("/admin", status_code=302)

# ================= API =================

@app.get("/Bendaryivas/api")
async def bendary_api(api_key: str, username: str, password: str):

    conn = get_db()
    key_data = conn.execute(
        "SELECT * FROM api_keys WHERE api_key = ?", (api_key,)
    ).fetchone()

    if not key_data:
        return JSONResponse({"error": "Invalid API key"}, status_code=403)

    expires_at = datetime.strptime(key_data["expires_at"], "%Y-%m-%d")
    if expires_at < datetime.now():
        return JSONResponse({"error": "API key expired"}, status_code=403)

    if key_data["usage_count"] >= key_data["max_devices"]:
        return JSONResponse({"error": "Device limit reached"}, status_code=403)

    conn.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE id=?",
        (key_data["id"],)
    )
    conn.commit()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:

        session_data = load_session()

        if session_data:
            client.cookies.update(session_data["cookies"])
            csrf_token = session_data["csrf_token"]
        else:
            cookies, csrf_token = await perform_login(client, username, password)
            if not csrf_token:
                return JSONResponse({"error": "Login failed"}, status_code=401)

        messages = await fetch_sms(client, csrf_token)

        if messages is None:
            cookies, csrf_token = await perform_login(client, username, password)
            if not csrf_token:
                return JSONResponse({"error": "Re-login failed"}, status_code=401)

            messages = await fetch_sms(client, csrf_token)

        return {
            "success": True,
            "owner": key_data["name"],
            "count": len(messages),
            "messages": messages
        }

# ================= RUN =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
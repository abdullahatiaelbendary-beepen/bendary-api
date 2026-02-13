from fastapi import FastAPI, Query, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta
import sqlite3
import secrets
import httpx
import re
from bs4 import BeautifulSoup
import os

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DB = "database.db"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123456")

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
            max_devices INTEGER DEFAULT 1,
            usage_count INTEGER DEFAULT 0,
            expires_at TEXT,
            created_at TEXT
        )
    """)
    conn.commit()

init_db()

# ================= HELPER FUNCTIONS =================

def extract_code(text):
    match = re.search(r"\b\d{3}[- ]?\d{3}\b", text)
    return match.group(0) if match else "N/A"

def extract_number(text):
    match = re.search(r"\b20\d{9,10}\b", text)
    return match.group(0) if match else "N/A"

# ================= FETCH SMS FROM IVAS =================

async def fetch_sms(username, password):
    LOGIN_URL = "https://ivas.tempnum.qzz.io/login"
    SMS_API_ENDPOINT = "https://ivas.tempnum.qzz.io/portal/sms/received/getsms"

    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:

        # 1️⃣ فتح صفحة تسجيل الدخول
        login_page = await client.get(LOGIN_URL, headers=headers)
        soup = BeautifulSoup(login_page.text, "html.parser")

        token_input = soup.find("input", {"name": "_token"})
        token = token_input["value"] if token_input else None

        if not token:
            return None

        # 2️⃣ تسجيل الدخول
        login_data = {
            "email": username,
            "password": password,
            "_token": token
        }

        login_res = await client.post(LOGIN_URL, data=login_data, headers=headers)

        if "login" in str(login_res.url):
            return None

        # 3️⃣ استخراج csrf token
        dashboard_soup = BeautifulSoup(login_res.text, "html.parser")
        csrf_meta = dashboard_soup.find("meta", {"name": "csrf-token"})

        if not csrf_meta:
            return None

        csrf_token = csrf_meta["content"]

        # 4️⃣ طلب الرسائل
        today = datetime.utcnow()
        yesterday = today - timedelta(days=1)

        payload = {
            "from": yesterday.strftime('%m/%d/%Y'),
            "to": today.strftime('%m/%d/%Y'),
            "_token": csrf_token
        }

        sms_response = await client.post(
            SMS_API_ENDPOINT,
            headers=headers,
            data=payload
        )

        soup = BeautifulSoup(sms_response.text, "html.parser")
        cards = soup.find_all("div", class_="card-body")

        results = []

        for card in cards:
            p = card.find("p")
            if not p:
                continue

            sms_text = p.get_text(strip=True)

            results.append({
                "number": extract_number(sms_text),
                "country": "Egypt",
                "flag": "🇪🇬",
                "code": extract_code(sms_text),
                "full_sms": sms_text,
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            })

        return results

# ================= ADMIN =================

@app.get("/admin", response_class=HTMLResponse)
async def admin_login_page():
    return """
    <h2>Admin Login</h2>
    <form method="post">
        <input type="password" name="password" placeholder="Admin Password" required/>
        <button type="submit">Login</button>
    </form>
    """

@app.post("/admin", response_class=HTMLResponse)
async def admin_login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return "<h3>Wrong Password</h3>"

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
    conn = get_db()
    api_key = "sk_" + secrets.token_hex(32)

    conn.execute("""
        INSERT INTO api_keys
        (name, api_key, max_devices, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (
        name,
        api_key,
        max_devices,
        expires_at,
        datetime.now().strftime("%Y-%m-%d")
    ))
    conn.commit()

    return RedirectResponse("/admin", status_code=302)

@app.post("/admin/delete")
async def delete_key(key_id: int = Form(...)):
    conn = get_db()
    conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    return RedirectResponse("/admin", status_code=302)

# ================= API =================

@app.get("/Bendaryivas/api.php")
async def bendary_api(
    api_key: str = Query(...),
    username: str = Query(...),
    password: str = Query(...)
):

    conn = get_db()

    key_data = conn.execute(
        "SELECT * FROM api_keys WHERE api_key=?",
        (api_key,)
    ).fetchone()

    if not key_data:
        return JSONResponse({"success": False, "error": "Invalid API key"}, status_code=403)

    if datetime.strptime(key_data["expires_at"], "%Y-%m-%d") < datetime.now():
        return JSONResponse({"success": False, "error": "API key expired"}, status_code=403)

    messages = await fetch_sms(username, password)

    if messages is None:
        return JSONResponse({"success": False, "error": "Login failed"}, status_code=401)

    conn.execute(
        "UPDATE api_keys SET usage_count = usage_count + 1 WHERE api_key=?",
        (api_key,)
    )
    conn.commit()

    return {
        "success": True,
        "key_owner": key_data["name"],
        "count": len(messages),
        "messages": messages
    }

# ================= RUN =================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
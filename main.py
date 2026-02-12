from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import sqlite3
import secrets
import httpx
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os

app = FastAPI()

DB = "database.db"

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
            expires_at TEXT,
            created_at TEXT
        )
    """)
    conn.commit()

init_db()

# ================= FETCH SMS =================

async def fetch_sms(username, password):
    LOGIN_URL = "https://ivas.tempnum.qzz.io/login"
    BASE_URL = "https://ivas.tempnum.qzz.io"
    SMS_API_ENDPOINT = "https://ivas.tempnum.qzz.io/portal/sms/received/getsms"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive"
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:

        # ===== GET LOGIN PAGE =====
        login_page = await client.get(LOGIN_URL, headers=headers)
        soup = BeautifulSoup(login_page.text, "html.parser")

        token_input = soup.find("input", {"name": "_token"})
        token = token_input["value"] if token_input else None

        login_data = {
            "email": username,
            "password": password,
        }

        if token:
            login_data["_token"] = token

        login_res = await client.post(LOGIN_URL, data=login_data, headers=headers)

        if "login" in str(login_res.url):
            return None

        dashboard_soup = BeautifulSoup(login_res.text, "html.parser")
        csrf_meta = dashboard_soup.find("meta", {"name": "csrf-token"})
        if not csrf_meta:
            return None

        csrf_token = csrf_meta["content"]

        # ===== FETCH SMS =====
        today = datetime.utcnow()
        yesterday = today - timedelta(days=1)

        payload = {
            "from": yesterday.strftime('%m/%d/%Y'),
            "to": today.strftime('%m/%d/%Y'),
            "_token": csrf_token
        }

        summary_response = await client.post(
            SMS_API_ENDPOINT,
            headers=headers,
            data=payload
        )

        soup = BeautifulSoup(summary_response.text, "html.parser")
        cards = soup.find_all("div", class_="card-body")

        results = []

        for card in cards:
            p = card.find("p")
            if not p:
                continue

            sms_text = p.get_text(strip=True)

            results.append({
                "date": str(datetime.now()),
                "number": "",
                "sms": sms_text,
                "status": "received"
            })

        return results


# ================= API ENDPOINT =================

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
        return JSONResponse({"error": "Invalid API key"}, status_code=403)

    expires_at = datetime.strptime(key_data["expires_at"], "%Y-%m-%d")
    if expires_at < datetime.now():
        return JSONResponse({"error": "API key expired"}, status_code=403)

    messages = await fetch_sms(username, password)

    if messages is None:
        return JSONResponse({"error": "Login failed"}, status_code=401)

    return {
        "success": True,
        "username": username,
        "timestamp": str(datetime.now()),
        "count": len(messages),
        "codes": messages
    }


# ================= RUN =================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
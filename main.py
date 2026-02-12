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

app = FastAPI()
templates = Jinja2Templates(directory="templates")

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
        max_devices INTEGER,
        expires_at TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()


init_db()

# ================= COUNTRY DETECT =================

def detect_country_from_number(phone_number: str):
    num = re.sub(r"\D", "", phone_number)
    if num.startswith("20"):
        return "Egypt", "🇪🇬"
    return "Unknown", "🏴‍☠️"

# ================= FETCH SMS =================

async def fetch_sms(client, headers, csrf_token):
    all_messages = []

    today = datetime.utcnow()
    start_date = today - timedelta(days=1)

    from_date = start_date.strftime('%m/%d/%Y')
    to_date = today.strftime('%m/%d/%Y')

    SMS_API_ENDPOINT = "https://ivas.tempnum.qzz.io/portal/sms/received/getsms"
    BASE_URL = "https://ivas.tempnum.qzz.io"

    payload = {
        'from': from_date,
        'to': to_date,
        '_token': csrf_token
    }

    summary_response = await client.post(SMS_API_ENDPOINT, headers=headers, data=payload)
    summary_soup = BeautifulSoup(summary_response.text, 'html.parser')

    group_divs = summary_soup.find_all('div', {'class': 'pointer'})
    if not group_divs:
        return []

    group_ids = []
    for div in group_divs:
        onclick = div.get('onclick', '')
        match = re.search(r"getDetials\('(.+?)'\)", onclick)
        if match:
            group_ids.append(match.group(1))

    numbers_url = urljoin(BASE_URL, "portal/sms/received/getsms/number")
    sms_url = urljoin(BASE_URL, "portal/sms/received/getsms/number/sms")

    for group_id in group_ids:
        numbers_payload = {
            'start': from_date,
            'end': to_date,
            'range': group_id,
            '_token': csrf_token
        }

        numbers_response = await client.post(numbers_url, headers=headers, data=numbers_payload)
        numbers_soup = BeautifulSoup(numbers_response.text, 'html.parser')

        number_divs = numbers_soup.select("div[onclick*='getDetialsNumber']")
        phone_numbers = [div.text.strip() for div in number_divs]

        for phone_number in phone_numbers:
            sms_payload = {
                'start': from_date,
                'end': to_date,
                'Number': phone_number,
                'Range': group_id,
                '_token': csrf_token
            }

            sms_response = await client.post(sms_url, headers=headers, data=sms_payload)
            sms_soup = BeautifulSoup(sms_response.text, 'html.parser')

            cards = sms_soup.find_all('div', class_='card-body')

            for card in cards:
                p = card.find('p', class_='mb-0')
                if not p:
                    continue

                sms_text = p.get_text(separator="\n").strip()
                code_match = re.search(r'\b(\d{4,8})\b', sms_text)
                code = code_match.group(1) if code_match else "N/A"

                country, flag = detect_country_from_number(phone_number)

                all_messages.append({
                    "number": phone_number,
                    "country": country,
                    "flag": flag,
                    "code": code,
                    "full_sms": sms_text,
                    "time": str(datetime.utcnow())
                })

    return all_messages

# ================= ADMIN PANEL =================

@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    conn = get_db()
    keys = conn.execute("SELECT * FROM api_keys").fetchall()
    conn.close()

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
    conn.close()

    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/delete")
async def delete_key(key_id: int = Form(...)):
    conn = get_db()
    conn.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    conn.commit()
    conn.close()

    return RedirectResponse("/admin", status_code=302)

# ================= API =================

@app.get("/Bendaryivas/api")
async def bendary_api(api_key: str, username: str, password: str):

    conn = get_db()
    key_data = conn.execute(
        "SELECT * FROM api_keys WHERE api_key = ?",
        (api_key,)
    ).fetchone()
    conn.close()

    if not key_data:
        return JSONResponse({"error": "Invalid API key"}, status_code=403)

    expires_at = datetime.strptime(key_data["expires_at"], "%Y-%m-%d")
    if expires_at < datetime.now():
        return JSONResponse({"error": "API key expired"}, status_code=403)

    LOGIN_URL = "https://ivas.tempnum.qzz.io/login"
    headers = {'User-Agent': 'Mozilla/5.0'}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:

        login_page = await client.get(LOGIN_URL, headers=headers)
        soup = BeautifulSoup(login_page.text, 'html.parser')

        token_input = soup.find('input', {'name': '_token'})

        login_data = {
            'email': username,
            'password': password
        }

        if token_input:
            login_data['_token'] = token_input['value']

        login_res = await client.post(LOGIN_URL, data=login_data, headers=headers)

        if "login" in str(login_res.url):
            return JSONResponse({"error": "Login failed"}, status_code=401)

        dashboard_soup = BeautifulSoup(login_res.text, 'html.parser')
        csrf_meta = dashboard_soup.find('meta', {'name': 'csrf-token'})

        if not csrf_meta:
            return JSONResponse({"error": "CSRF token not found"}, status_code=500)

        csrf_token = csrf_meta.get('content')
        headers['Referer'] = str(login_res.url)

        messages = await fetch_sms(client, headers, csrf_token)

        return {
            "success": True,
            "key_owner": key_data["name"],
            "count": len(messages),
            "messages": messages
        }

# ================= RUN SERVER =================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
# -*- coding: utf-8 -*-

import os
import re
import html
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ==============================
# CONFIG
# ==============================

LOGIN_URL = "https://ivas.tempnum.qzz.io/login"
SMS_URL = "https://ivas.tempnum.qzz.io/portal/sms/received/getsms"

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

API_KEYS = {
    "sk_2f06985b4df1ba92d21893d9a680220da573a957e913463efaf7a2306e9fee49": {
        "name": "Bendary",
        "active": True
    }
}

# ==============================
# Helpers
# ==============================

def extract_code(text):
    text = text.replace("-", "")
    match = re.search(r"\b\d{3,8}\b", text)
    return match.group(0) if match else "N/A"


async def login_and_fetch(username, password):
    async with httpx.AsyncClient(follow_redirects=True) as client:

        # تسجيل الدخول
        login_data = {
            "email": username,
            "password": password
        }

        login_response = await client.post(LOGIN_URL, data=login_data)

        if login_response.status_code != 200:
            return {"error": "Login failed"}

        # جلب الرسائل
        sms_response = await client.get(SMS_URL)

        if sms_response.status_code != 200:
            return {"error": "Failed to fetch SMS"}

        soup = BeautifulSoup(sms_response.text, "html.parser")
        messages = []

        rows = soup.find_all("tr")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 3:
                continue

            number = cols[0].get_text(strip=True)
            sms_text = html.unescape(cols[1].get_text())
            sms_text = sms_text.replace("\n", " ").strip()
            time_text = cols[2].get_text(strip=True)

            messages.append({
                "number": number,
                "country": "Egypt",
                "flag": "🇪🇬",
                "code": extract_code(sms_text),
                "full_sms": sms_text,
                "time": time_text
            })

        return messages


# ==============================
# API Endpoint
# ==============================

@app.get("/Bendaryivas/api")
async def api_endpoint(api_key: str, username: str, password: str):

    if api_key not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API Key")

    if not API_KEYS[api_key]["active"]:
        raise HTTPException(status_code=403, detail="API Key Disabled")

    result = await login_and_fetch(username, password)

    if isinstance(result, dict) and "error" in result:
        return JSONResponse({
            "status": "error",
            "message": result["error"]
        })

    return JSONResponse({
        "status": "success",
        "owner": API_KEYS[api_key]["name"],
        "total_messages": len(result),
        "server_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        "data": result
    })


# ==============================
# Admin Login Page
# ==============================

@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/admin/login")
async def admin_login(request: Request, username: str = Form(...), password: str = Form(...)):

    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        return RedirectResponse("/admin/dashboard", status_code=302)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Wrong credentials"
    })


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "keys": API_KEYS
    })
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, request, abort
import requests
from pymongo import MongoClient
from pymongo.server_api import ServerApi

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config — all pulled from environment variables. Don't hardcode secrets.
# ---------------------------------------------------------------------------
LINQ_API_KEY = "linq_jvgXUWb9aTGVbjizkIC0A3W4ATr7Jaxx"
MONGO_URI = "mongodb+srv://iainhmacdonald_db_user:oOcXTTeobAdpM1EF@cluster0.ykdex3l.mongodb.net/?appName=Cluster0"
BASE_URL = "https://reassign-rogue-swaddling.ngrok-free.dev"  # e.g. https://reassign-rogue-swaddling.ngrok-free.dev

TOKEN_TTL_MINUTES = 5

client = MongoClient(MONGO_URI, server_api=ServerApi("1"))
db = client["myAppDB"]
users = db["users"]
users.create_index("PhoneNumber", unique=True)
users.create_index("cred_token", unique=True, sparse=True)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
def send_message(to_phone, text):
    data = {
        "from": "+16462397830",
        "to": [to_phone],
        "message": {
            "parts": [
                {"type": "text", "value": text}
            ]
        },
    }
    response = requests.post(
        "https://api.linqapp.com/v3/messages",
        headers={
            "Authorization": f"Bearer {LINQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json=data,
    )
    print("LINQ response:", response.status_code)
    print(response.text)
    return response


# ---------------------------------------------------------------------------
# Secure link generation
# ---------------------------------------------------------------------------
def issue_credential_link(userphone):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)

    users.update_one(
        {"PhoneNumber": userphone},
        {
            "$set": {
                "PhoneNumber": userphone,
                "cred_token": token,
                "cred_token_expires": expires,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    return f"{BASE_URL}/collect/{token}"


FORM_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link your DoorDash account</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background:#f5f5f7; margin:0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#fff; padding:32px; border-radius:12px; box-shadow:0 2px 12px rgba(0,0,0,.08);
           width:100%; max-width:360px; }}
  h1 {{ font-size:18px; margin:0 0 16px; }}
  label {{ display:block; font-size:13px; color:#555; margin:12px 0 4px; }}
  input {{ width:100%; padding:10px; border:1px solid #ccc; border-radius:8px; font-size:15px;
           box-sizing:border-box; }}
  button {{ margin-top:20px; width:100%; padding:12px; border:none; border-radius:8px;
            background:#111; color:#fff; font-size:15px; cursor:pointer; }}
  .note {{ font-size:12px; color:#888; margin-top:12px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>Link your DoorDash account</h1>
    <form method="POST" action="/collect/{token}">
      <label for="u">DoorDash username</label>
      <input id="u" name="username" type="text" autocomplete="username" required>
      <label for="p">DoorDash password</label>
      <input id="p" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Save</button>
    </form>
    <p class="note">This link expires in {ttl} minutes and can only be used once.</p>
  </div>
</body>
</html>"""

EXPIRED_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Link expired</title></head>
<body style="font-family:sans-serif;text-align:center;padding-top:80px;">
<h2>This link has expired or was already used.</h2>
<p>Text "order" again to get a new one.</p>
</body></html>"""

SUCCESS_PAGE = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Saved</title></head>
<body style="font-family:sans-serif;text-align:center;padding-top:80px;">
<h2>Account linked. You can close this page.</h2>
</body></html>"""


def get_valid_token_user(token):
    user = users.find_one({"cred_token": token})
    if not user:
        return None
    expires = user.get("cred_token_expires")
    if not expires:
        return None
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        return None
    return user


@app.route("/collect/<token>", methods=["GET"])
def collect_form(token):
    user = get_valid_token_user(token)
    if not user:
        return EXPIRED_PAGE, 410
    return FORM_PAGE.format(token=token, ttl=TOKEN_TTL_MINUTES)


@app.route("/collect/<token>", methods=["POST"])
def collect_submit(token):
    user = get_valid_token_user(token)
    if not user:
        return EXPIRED_PAGE, 410

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        abort(400)

    users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "doordashusername": username,
                "doordashpassword": password,
                "pending_step": None,
                "updatedAt": datetime.now(timezone.utc),
            },
            "$unset": {
                "cred_token": "",
                "cred_token_expires": "",
            },
        },
    )

    send_message(user["PhoneNumber"], "Got it — your DoorDash account is linked. Please continue.")
    return SUCCESS_PAGE, 200


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
@app.route("/linq-webhook", methods=["POST"])
def linq_webhook():
    payload = request.json

    parts = payload.get("data", {}).get("parts", [])
    userphone = payload["data"]["sender_handle"]["handle"]

    for part in parts:
        if part.get("type") != "text":
            continue

        message = part.get("value", "").strip()
        print(f"You texted: {message}")

        user = users.find_one({"PhoneNumber": userphone})

        # Normal message handling
        if "order" in message.lower():
            print("Sending a response back to the user...")

            has_creds = user and user.get("doordashusername") and user.get("doordashpassword")

            if has_creds:
                send_message(userphone, "Account details already present please continue")
            else:
                link = issue_credential_link(userphone)
                send_message(
                    userphone,
                    f"To link your DoorDash account, tap this secure link: {link}\n"
                    f"It expires in {TOKEN_TTL_MINUTES} minutes.",
                )
            continue

    return "", 200


@app.route("/", methods=["GET"])
def home():
    return "Linq webhook is running!"


if __name__ == "__main__":
    app.run(port=5000)
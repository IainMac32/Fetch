import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import Flask, request, abort
import requests
from pymongo import MongoClient
from pymongo.server_api import ServerApi
import json
from pymongo.errors import DuplicateKeyError

import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config — all pulled from environment variables. Don't hardcode secrets.
# ---------------------------------------------------------------------------
LINQ_API_KEY = os.getenv("LINQ_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")
BASE_URL = os.getenv("BASE_URL")  # e.g. https://reassign-rogue-swaddling.ngrok-free.dev

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TOKEN_TTL_MINUTES = 5

import certifi
client = MongoClient(MONGO_URI, server_api=ServerApi("1"))
db = client["myAppDB"]
orders = db["orders"]
users = db["users"]
users.create_index("PhoneNumber", unique=True)
users.create_index("cred_token", unique=True, sparse=True)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
def classify_doordash_instruction(userphone, message):
    history = get_recent_history(userphone)
    history_text = "\n".join(f'{h["role"]}: {h["text"]}' for h in history)

    past_orders = get_past_orders(userphone)
    if past_orders:
        past_orders_text = "\n".join(
            f'- {o["order"]} (ordered {o["createdAt"].strftime("%Y-%m-%d")})'
            for o in past_orders
        )
    else:
        past_orders_text = "(no past orders yet)"

    prompt = f"""
    You are a friendly, casual texting assistant for a DoorDash ordering service.
    You're chatting with a real person over iMessage — keep replies short, warm,
    and natural, like a helpful friend texting back.

    Here is this user's past order history (most recent first), which you can
    use to speed things up — e.g. if they order something they've ordered
    before, offer to repeat their usual instead of asking from scratch:

    {past_orders_text}

    You are having an ONGOING conversation. Here is the recent history
    (oldest first). The last line is the newest message from the user:

    {history_text}
    user: {message}

    Your job: figure out if you now have EVERYTHING needed to place a real
    DoorDash order. A complete order needs, at minimum:
    - the restaurant or store
    - specific item(s) — not just "pizza" but what kind
    - size/quantity for each item where relevant
    - any must-have modifiers (e.g. toppings) if the item implies a choice

    Categories:

    DOORDASH_ACTION
    Use ONLY when the conversation now has enough specific detail to actually
    place the order with no more questions needed.

    CHAT_RESPONSE
    Use this for greetings, thanks, general questions, OR when the user wants
    to order something but you're still missing details. In that case, ask ONE
    short, casual, specific follow-up question for the most important missing
    piece — don't ask for everything at once. If their past orders suggest an
    obvious match (e.g. they always order the same thing from this restaurant),
    offer that as the question instead of asking generically.

    Examples:

    history: (empty)
    past orders: (none)
    user: "I want to order pizza from dominos"
    => CHAT_RESPONSE, reply: "Nice, Domino's it is! What kind of pizza and what size?"

    history:
    user: I want to order pizza from dominos
    assistant: Nice, Domino's it is! What kind of pizza and what size?
    user: "large pepperoni"
    => DOORDASH_ACTION, action_instruction: "Order a large pepperoni pizza from Domino's"

    history: (empty)
    past orders:
    - Order a large pepperoni pizza from Domino's (ordered 2026-09-10)
    user: "I want pizza from dominos again"
    => CHAT_RESPONSE, reply: "Want the usual — large pepperoni? 🍕"

    history:
    assistant: Want the usual — large pepperoni? 🍕
    user: "yeah"
    => DOORDASH_ACTION, action_instruction: "Order a large pepperoni pizza from Domino's"

    user: "order"
    => CHAT_RESPONSE, reply: "Hey! What are you in the mood for? 🍔"

    user: "Thanks"
    => CHAT_RESPONSE, reply: "Anytime! 🙌"

    Return ONLY valid JSON with this exact shape:

    {{
        "category": "DOORDASH_ACTION" or "CHAT_RESPONSE",
        "action_instruction": "complete order instruction if DOORDASH_ACTION, otherwise null",
        "reply": "short, friendly, natural text to send the user",
        "reason": "short explanation"
    }}
    """

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-5.6-terra",
            "input": prompt
        },
        timeout=30
    )

    print("Status:", response.status_code)

    if response.status_code != 200:
        print(response.text)
        return None

    data = response.json()
    result = data["output"][0]["content"][0]["text"]
    return json.loads(result)

#memory
conversations = db["conversations"]

def get_recent_history(userphone, limit=10):
    convo = conversations.find_one({"PhoneNumber": userphone})
    if not convo:
        return []
    return convo.get("history", [])[-limit:]

def append_history(userphone, role, text):
    conversations.update_one(
        {"PhoneNumber": userphone},
        {
            "$push": {"history": {"role": role, "text": text, "at": datetime.now(timezone.utc)}},
            "$set": {"updatedAt": datetime.now(timezone.utc)},
        },
        upsert=True,
    )

#past orders
def get_past_orders(userphone, limit=5):
    cursor = orders.find({"PhoneNumber": userphone}).sort("createdAt", -1).limit(limit)
    return list(cursor)

# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
def send_message(to_phone, text):
    data = {
        "from": "+14046630503",
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
processed_messages = db["processed_messages"]
processed_messages.create_index("message_id", unique=True)

@app.route("/linq-webhook", methods=["POST"])
def linq_webhook():
    payload = request.json
    data = payload.get("data", {})

    # Only process genuine inbound messages from the customer.
    # This drops echoes of our own bot replies.
    if data.get("direction") != "inbound":
        return "", 200
    if data.get("sender_handle", {}).get("is_me"):
        return "", 200

    message_id = data.get("id")
    if message_id:
        try:
            processed_messages.insert_one({"message_id": message_id})
        except DuplicateKeyError:
            print(f"Duplicate webhook for message_id={message_id}, skipping")
            return "", 200

    parts = data.get("parts", [])
    userphone = data["sender_handle"]["handle"]

    for part in parts:
        if part.get("type") != "text":
            continue

        message = part.get("value", "").strip()
        print(f"You texted: {message}")

        user = users.find_one({"PhoneNumber": userphone})

        has_creds = user and user.get("doordashusername") and user.get("doordashpassword")

        # Route to the ordering flow if this message mentions "order",
        # OR if the user is already mid-conversation about an order
        # (so follow-up replies like "large pepperoni" still get handled).
        in_progress = bool(get_recent_history(userphone))
        wants_order = "order" in message.lower() or in_progress

        if wants_order:
            if has_creds:
                result = classify_doordash_instruction(userphone, message)
                print("Classification result:", result)

                if result:
                    append_history(userphone, "user", message)
                    append_history(userphone, "assistant", result["reply"])
                    send_message(userphone, result["reply"])

                    if result["category"] == "DOORDASH_ACTION":
                        orders.insert_one({
                            "PhoneNumber": userphone,
                            "order": result["action_instruction"],
                            "status": "pending",
                            "createdAt": datetime.now(timezone.utc),
                        })
                        # hand result["action_instruction"] to your actual
                        # ordering logic here once that's built
                        conversations.update_one(
                            {"PhoneNumber": userphone},
                            {"$set": {"history": []}},  # reset for next order
                        )

                        
                else:
                    send_message(userphone, "Sorry, something went wrong — try again?")
            else:
                link = issue_credential_link(userphone)
                send_message(
                    userphone,
                    f"To link your DoorDash account, tap this secure link: {link}\n"
                    f"It expires in {TOKEN_TTL_MINUTES} minutes.",
                )
        break

    return "", 200

@app.route("/", methods=["GET"])
def home():
    return "Linq webhook is running!"

"""Run the LINQ grocery search webhook."""
import logging
from shopper.app import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # No reloader: one process owns active searches and recent webhook IDs.
    create_app().run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests


def verify_signature(secret, body, headers, *, now=None):
    """LINQ Standard Webhooks signature, verified before JSON parsing."""
    try:
        timestamp = headers["webhook-timestamp"]
        event_id = headers["webhook-id"]
        signature = headers["webhook-signature"]
        if abs((time.time() if now is None else now) - int(timestamp)) > 300:
            return False
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
        if not key or not event_id:
            return False
        expected = base64.b64encode(hmac.digest(key, f"{event_id}.{timestamp}.".encode() + body, "sha256")).decode()
        return any(part.startswith("v1,") and hmac.compare_digest(expected, part[3:]) for part in signature.split())
    except (KeyError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class IncomingMessage:
    event_id: str
    chat_id: str
    user_key: str
    text: str
    sender_handle: str


def incoming_message(payload):
    """Only private inbound text events; support LINQ's 2025 and 2026 formats."""
    if not isinstance(payload, dict) or payload.get("event_type") != "message.received":
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    chat = data.get("chat") or {}
    sender = data.get("sender_handle") or data.get("from_handle") or {}
    message = data.get("message") or data
    if not all(isinstance(value, dict) for value in (chat, sender, message)):
        return None
    if (data.get("direction", "inbound") != "inbound" or data.get("is_from_me")
            or sender.get("is_me") or chat.get("is_group", data.get("is_group")) is not False):
        return None
    chat_id = chat.get("id") or data.get("chat_id")
    handle = sender.get("handle") or data.get("from")
    event_id = payload.get("event_id")
    if not all(isinstance(value, str) and value for value in (chat_id, handle, event_id)):
        return None
    parts = message.get("parts")
    if not isinstance(parts, list):
        return None
    text = "\n".join(part["value"] for part in parts if isinstance(part, dict)
                     and part.get("type") == "text" and isinstance(part.get("value"), str)).strip()
    if not text or len(text) > 2000:
        return None
    identity = f"{payload.get('partner_id', '')}:{handle}"
    return IncomingMessage(event_id, chat_id, hashlib.sha256(identity.encode()).hexdigest(), text, handle)


class LinqClient:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def send(self, chat_id, text):
        response = requests.post(
            f"{self.base_url}/chats/{quote(chat_id, safe='')}/messages",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"parts": [{"type": "text", "value": text}]},
            timeout=(5, 15),
        )
        response.raise_for_status()

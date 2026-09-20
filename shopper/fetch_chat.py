"""OpenAI-backed chat helper for the Fetch calendar UI."""

import json
import re

import requests


class FetchChatError(Exception):
    """Safe error raised when the Fetch chat endpoint cannot complete."""


PREFERENCE_LIST_NAMES = {"dietaryRestrictions", "favouriteFoods", "avoidFoods"}


def _extract_text(data):
    if data.get("status") != "completed":
        raise FetchChatError("The AI response was incomplete. Please try again.")
    texts = []
    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part.get("type") == "text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
    if not texts:
        raise FetchChatError("The AI response was empty. Please try again.")
    return "\n".join(texts)


def _normalize_updates(value):
    normalized = []
    if not isinstance(value, list):
        return normalized
    for item in value:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        date = item.get("date")
        note = item.get("note")
        if action not in {"upsert", "remove"}:
            continue
        if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        if action == "remove":
            normalized.append({"action": "remove", "date": date, "note": ""})
            continue
        if not isinstance(note, str):
            continue
        text = note.strip()
        if not text:
            continue
        normalized.append({"action": "upsert", "date": date, "note": text})
    return normalized


def _normalize_preference_updates(value):
    normalized = []
    if not isinstance(value, list):
        return normalized
    for item in value:
        if not isinstance(item, dict):
            continue
        action = item.get("action")
        list_name = item.get("list")
        raw_value = item.get("value")
        if action not in {"add", "remove", "toggle"}:
            continue
        if list_name not in PREFERENCE_LIST_NAMES:
            continue
        if not isinstance(raw_value, str):
            continue
        value_text = raw_value.strip()
        if not value_text:
            continue
        normalized.append({"action": action, "list": list_name, "value": value_text})
    return normalized


def run_fetch_chat(*, api_key, model, message, conversation, state):
    if not api_key:
        raise FetchChatError("OPENAI_API_KEY is not configured on the server.")

    today = state.get("exportedAt", "")[:10] if isinstance(state, dict) else ""
    instructions = (
        "You are Fetch Concierge, a friendly food-planning assistant replying inside an iMessage thread. "
        "Keep replies short, warm, and practical. You can chat about meals, preferences, and calendar plans. "
        "When the user clearly asks to add or schedule something on a date, return calendar_updates with action upsert, "
        "ISO dates in YYYY-MM-DD format, and concise note text. When the user clearly asks to remove, delete, clear, or "
        "unschedule a calendar item, return calendar_updates with action remove for the affected date and note as null. "
        "When the user clearly asks to add or remove a dietary filter, selected restriction, favorite food, or avoid item, "
        "return preference_updates entries with list set to dietaryRestrictions, favouriteFoods, or avoidFoods, plus action add or remove and the exact value text. "
        "When the user clearly asks to toggle a dietary filter on or off, you may return action toggle for dietaryRestrictions. "
        "Treat selected restrictions and dietary filters as the same dietaryRestrictions list. "
        "Use selectedDate or explicit dates from the request when helpful. "
        "If the request is ambiguous, ask one short follow-up question instead of guessing. "
        f"Treat today as {today or 'unknown'} if relative dates are used."
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["reply", "calendar_updates", "preference_updates", "selected_date"],
        "properties": {
            "reply": {"type": "string", "maxLength": 280},
            "selected_date": {"type": ["string", "null"], "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
            "calendar_updates": {
                "type": "array",
                "maxItems": 10,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "date", "note"],
                    "properties": {
                        "action": {"type": "string", "enum": ["upsert", "remove"]},
                        "date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                        "note": {"type": ["string", "null"], "maxLength": 240}
                    }
                }
            },
            "preference_updates": {
                "type": "array",
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["action", "list", "value"],
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "remove", "toggle"]},
                        "list": {"type": "string", "enum": ["dietaryRestrictions", "favouriteFoods", "avoidFoods"]},
                        "value": {"type": "string", "maxLength": 120}
                    }
                }
            }
        }
    }
    payload = {
        "model": model,
        "store": False,
        "max_output_tokens": 500,
        "input": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps({
                    "message": message,
                    "conversation": conversation[-10:] if isinstance(conversation, list) else [],
                    "state": state,
                })
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "fetch_chat_reply",
                "strict": True,
                "schema": schema,
            }
        }
    }
    try:
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(5, 60),
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in {401, 402, 403}:
            raise FetchChatError("The AI service rejected the server credentials. Check API access and billing.") from None
        raise FetchChatError("The AI service could not finish the request. Please try again.") from None
    except ValueError:
        raise FetchChatError("The AI service returned an unreadable response. Please try again.") from None

    try:
        result = json.loads(_extract_text(data))
    except (TypeError, ValueError, json.JSONDecodeError):
        raise FetchChatError("The AI returned malformed chat data. Please try again.") from None

    if not isinstance(result, dict):
        raise FetchChatError("The AI returned malformed chat data. Please try again.")

    reply = result.get("reply")
    selected_date = result.get("selected_date")
    if not isinstance(reply, str) or not reply.strip():
        raise FetchChatError("The AI did not return a usable reply. Please try again.")
    if selected_date is not None and (not isinstance(selected_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", selected_date)):
        selected_date = None

    return {
        "reply": reply.strip(),
        "selected_date": selected_date,
        "calendar_updates": _normalize_updates(result.get("calendar_updates")),
        "preference_updates": _normalize_preference_updates(result.get("preference_updates")),
    }
"""Helpers for importing stored orders into the Fetch calendar."""

from datetime import datetime, timezone

import certifi


class FetchOrdersError(Exception):
    """Safe error raised when order import cannot complete."""


def _describe_order_read_failure(error):
    message = str(error or "")
    normalized = message.lower()

    if "tlsv1_alert" in normalized or "ssl handshake failed" in normalized or "tls handshake" in normalized:
        return "The order database connection failed during the TLS handshake."

    if "serverselectiontimeout" in normalized or "timed out" in normalized:
        return "The order database timed out while connecting."

    return "The order database could not be read right now."


def _order_date_key(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.date().isoformat()
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return None


def load_order_calendar_entries(*, mongo_uri, phone_number, db_name="myAppDB", limit=120):
    if not mongo_uri:
        raise FetchOrdersError("MONGO_URI is not configured on the server.")
    try:
        from pymongo import MongoClient
    except ImportError:
        raise FetchOrdersError("pymongo is not installed on the server.") from None

    try:
        client = MongoClient(mongo_uri, tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
        collection = client[db_name]["orders"]
        cursor = collection.find(
            {"PhoneNumber": phone_number},
            {"_id": 0, "order": 1, "createdAt": 1},
        ).sort("createdAt", 1).limit(limit)
        entries = {}
        for item in cursor:
            text = str(item.get("order") or "").strip()
            date_key = _order_date_key(item.get("createdAt"))
            if not text or not date_key:
                continue
            line = f"Order: {text}"
            existing = entries.get(date_key)
            if not existing:
                entries[date_key] = line
            elif line not in existing.split("\n"):
                entries[date_key] = f"{existing}\n{line}"
        return entries
    except FetchOrdersError:
        raise
    except Exception as exc:
        raise FetchOrdersError(_describe_order_read_failure(exc)) from None
    finally:
        if "client" in locals():
            client.close()
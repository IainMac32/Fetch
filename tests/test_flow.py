import base64
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from shopper.app import create_app
from shopper.config import Settings
from shopper.events import RecentEvents
from shopper.linq import LinqClient, incoming_message, verify_signature

SECRET = "whsec_" + base64.b64encode(b"local-test-signing-secret").decode()


def payload(event_id="event-1", text="search"):
    return {"event_type": "message.received", "event_id": event_id, "partner_id": "partner-1",
            "data": {"direction": "inbound", "chat": {"id": "chat-a", "is_group": False},
                     "sender_handle": {"handle": "+15550000001", "is_me": False},
                     "parts": [{"type": "text", "value": text}]}}


def headers(body, event_id="event-1", timestamp=None):
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    sig = hmac.digest(b"local-test-signing-secret", f"{event_id}.{timestamp}.".encode() + body, "sha256")
    return {"webhook-id": event_id, "webhook-timestamp": timestamp,
            "webhook-signature": "v1," + base64.b64encode(sig).decode(), "Content-Type": "application/json"}


@pytest.fixture
def config():
    return Settings(browserbase_api_key="test", linq_webhook_secret=SECRET,
                    demo_user_handle="+15550000001")


def test_signatures_require_original_body_and_fresh_timestamp():
    body = json.dumps(payload()).encode()
    assert verify_signature(SECRET, body, headers(body))
    assert not verify_signature(SECRET, body + b" ", headers(body))
    assert not verify_signature(SECRET, body, headers(body, timestamp=time.time() - 301))
    assert not verify_signature(SECRET, body, headers(body, timestamp=time.time() + 301))
    assert not verify_signature("", body, headers(body))
    assert not verify_signature(SECRET, body, {})


def test_key_rotation_accepts_any_matching_v1_signature():
    body = b"test"
    values = headers(body)
    values["webhook-signature"] = "v1,bad " + values["webhook-signature"]
    assert verify_signature(SECRET, body, values)


def test_current_and_legacy_payloads_have_same_identity():
    current = incoming_message(payload())
    legacy = {"event_type": "message.received", "event_id": "event-2", "partner_id": "partner-1", "data": {
        "chat_id": "chat-b", "is_group": False, "is_from_me": False, "from": "+15550000001",
        "message": {"parts": [{"type": "text", "value": "search"}]}}}
    assert incoming_message(legacy).user_key == current.user_key
    assert current.chat_id == "chat-a"
    assert "+1555" not in current.user_key


@pytest.mark.parametrize("change", [
    lambda p: p.update(event_type="message.sent"),
    lambda p: p["data"].update(direction="outbound"),
    lambda p: p["data"]["chat"].update(is_group=True),
    lambda p: p["data"]["chat"].pop("is_group"),
    lambda p: p["data"]["sender_handle"].update(is_me=True),
    lambda p: p["data"].update(parts=None),
    lambda p: p["data"].update(chat="bad"),
    lambda p: p.update(data=[]),
])
def test_ignore_unsafe_or_non_message_events(change):
    data = payload()
    change(data)
    assert incoming_message(data) is None


def test_webhook_acknowledges_and_deduplicates_before_background_work(config):
    service = MagicMock()
    service.submit.return_value = True
    app = create_app(config, service=service)
    client = app.test_client()
    body = json.dumps(payload()).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    assert service.submit.call_count == 1
    assert client.post("/linq-webhook", json=payload()).status_code == 401


def test_busy_worker_allows_webhook_retry(config):
    service = MagicMock()
    service.submit.side_effect = [False, True]
    client = create_app(config, service=service).test_client()
    body = json.dumps(payload()).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 503
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200


def test_linq_replies_to_originating_chat(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr("shopper.linq.requests.post", post)
    LinqClient("test", "https://api.example/v3").send("chat-a", "Connect here")
    assert post.call_args.args[0] == "https://api.example/v3/chats/chat-a/messages"
    assert post.call_args.kwargs["json"] == {"message": {"parts": [{"type": "text", "value": "Connect here"}]}}
    assert "timeout" in post.call_args.kwargs


def test_recent_events_expire_without_evicting_live_duplicates():
    now = [0]
    events = RecentEvents(ttl_seconds=10, capacity=2, clock=lambda: now[0])
    assert events.claim("first") is True
    now[0] = 1
    assert events.claim("second") is True
    assert events.claim("first") is False
    assert events.claim("third") is None
    now[0] = 10
    assert events.claim("third") is True
    assert events.claim("second") is False
    events.forget("third")
    assert events.claim("third") is True


def test_recent_events_claim_is_atomic():
    events = RecentEvents()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: events.claim("same-event"), range(32)))
    assert results.count(True) == 1
    assert results.count(False) == 31


def test_full_event_cache_requests_retry_and_still_accepts_duplicates(config):
    service = MagicMock()
    events = RecentEvents(capacity=1)
    client = create_app(config, service=service, events=events).test_client()
    body = json.dumps(payload()).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    other = json.dumps(payload(event_id="event-2")).encode()
    assert client.post("/linq-webhook", data=other, headers=headers(other, "event-2")).status_code == 503
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    assert service.submit.call_count == 1


def test_other_senders_do_not_consume_cache_or_submit_work(config):
    service = MagicMock()
    events = RecentEvents(capacity=1)
    client = create_app(config, service=service, events=events).test_client()
    data = payload()
    data["data"]["sender_handle"]["handle"] = "+15550000002"
    body = json.dumps(data).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    service.submit.assert_not_called()
    assert events.claim("event-1") is True


def test_webhook_rejects_signed_invalid_json_and_mismatched_id(config):
    service = MagicMock()
    client = create_app(config, service=service).test_client()
    for body in (b"[]", b"invalid", json.dumps(payload(event_id="different")).encode()):
        assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 400
    service.submit.assert_not_called()


def test_app_starts_without_database_or_credential_routes(config):
    app = create_app(config)
    try:
        client = app.test_client()
        response = client.get("/")
        assert response.status_code == 200
        assert "SEARCH" in response.json["message"]
        assert response.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert client.get("/connect").status_code == 404
        assert client.get("/api/handoff").status_code == 404
        assert client.post("/api/handoff/credentials", json={}).status_code == 404
        assert client.post("/api/handoff/code", json={}).status_code == 404
        assert client.get("/static/connect.js").status_code == 404
    finally:
        app.extensions["search"].close()

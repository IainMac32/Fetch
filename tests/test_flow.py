import base64
import hashlib
import hmac
import json
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
import mongomock

from shopper.app import create_app
from shopper.browser import DoorDashBrowser, LoginCancelled, LoginExpired, is_doordash, signed_in, wait_for_login
from shopper.config import Settings
from shopper.linq import LinqClient, incoming_message, verify_signature
from shopper.service import ConnectionService, LoginJob
from shopper.store import Store

SECRET = "whsec_" + base64.b64encode(b"local-test-signing-secret").decode()
FERNET_KEY = Fernet.generate_key().decode()


def memory_store(key=FERNET_KEY, client=None):
    return Store("mongodb://unused", key, client=client or mongomock.MongoClient())


def payload(event_id="event-1", text="connect"):
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
def config(tmp_path):
    return Settings(browserbase_api_key="test", linq_webhook_secret=SECRET,
                    public_base_url="https://shop.example.com",
                    credential_encryption_key=FERNET_KEY, demo_user_handle="+15550000001")


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
        "message": {"parts": [{"type": "text", "value": "connect"}]}}}
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


def test_store_survives_restart_and_isolates_accounts(tmp_path):
    client = mongomock.MongoClient()
    store = memory_store(client=client)
    store.save_context("alice", "context-a")
    store.save_context("bob", "context-b")
    store.defer_context("alice", 1234)
    restarted = memory_store(client=client)
    assert restarted.context_for("alice") == ("context-a", 1234)
    assert restarted.context_for("bob") == ("context-b", 0)
    assert restarted.claim_event("event")
    assert not store.claim_event("event")
    store.forget_event("event")
    assert restarted.claim_event("event")


def test_credentials_are_encrypted_and_survive_restart(tmp_path):
    client = mongomock.MongoClient()
    store = memory_store(client=client)
    store.save_credentials("demo-user", "user@example.test", "door-dash-password")
    raw_document = store.db.doordash_credentials.find_one({"user_key": "demo-user"})
    assert b"user@example.test" not in raw_document["login_ciphertext"]
    assert b"door-dash-password" not in raw_document["password_ciphertext"]
    restarted = memory_store(client=client)
    assert restarted.credentials_for("demo-user") == ("user@example.test", "door-dash-password")
    with pytest.raises(RuntimeError, match="DOORDASH_CREDENTIAL_KEY"):
        memory_store(Fernet.generate_key().decode(), client=client).credentials_for("demo-user")
    restarted.forget_credentials("demo-user")
    assert restarted.credentials_for("demo-user") is None


def test_webhook_acknowledges_and_deduplicates_before_background_work(config):
    service = MagicMock()
    service.submit.return_value = True
    app = create_app(config, service=service, store=memory_store(config.credential_encryption_key))
    client = app.test_client()
    body = json.dumps(payload()).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
    assert service.submit.call_count == 1
    assert client.post("/linq-webhook", json=payload()).status_code == 401


def test_busy_worker_allows_webhook_retry(config):
    service = MagicMock()
    service.submit.side_effect = [False, True]
    client = create_app(config, service=service, store=memory_store(config.credential_encryption_key)).test_client()
    body = json.dumps(payload()).encode()
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 503
    assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200


def test_credential_handoff_encrypts_saved_values_and_requires_token(config):
    job = LoginJob("alice", "chat-a", config.public_base_url)
    job.begin_credentials(60)
    service = MagicMock()
    service.lookup.side_effect = lambda token: job if token == job.token else None
    store = memory_store(config.credential_encryption_key)
    client = create_app(config, service=service, store=store).test_client()
    auth = {"Authorization": "Bearer " + job.token}
    assert client.get("/api/handoff").status_code == 410
    assert client.get("/api/handoff", headers={"Authorization": "Bearer bob"}).status_code == 410
    assert client.get("/api/handoff", headers=auth).json["state"] == "awaiting_credentials"
    response = client.post("/api/handoff/credentials", headers=auth,
                           json={"login": "user@example.test", "password": "door-dash-password"})
    assert response.status_code == 202
    action, queued = job.commands.get_nowait()
    assert action == "credentials"
    assert queued == {"login": "user@example.test", "password": "door-dash-password"}
    assert store.credentials_for("alice") == ("user@example.test", "door-dash-password")
    assert client.post("/api/handoff/credentials", headers={**auth, "Origin": "https://evil.example"},
                       json={"login": "user@example.test", "password": "x"}).status_code == 403
    job.transition("connected", "Connected")
    assert client.post("/api/handoff/credentials", headers=auth,
                       json={"login": "user@example.test", "password": "x"}).status_code == 409


def test_expired_cancelled_and_full_handoff_reject_input():
    job = LoginJob("alice", "chat", "https://example.com")
    job.begin_credentials(60)
    assert job.submit_sign_in("a@b.test", "x", lambda: None)
    assert not job.submit_sign_in("a@b.test", "x", lambda: None)
    job.deadline = time.monotonic() - 1
    assert not job.enqueue("code", "123456")
    job.begin_credentials(60)
    job.cancelled.set()
    assert not job.snapshot()["can_control"]


def test_connection_page_never_embeds_tokens_and_is_not_cached(config):
    client = create_app(config, service=MagicMock(), store=memory_store(config.credential_encryption_key)).test_client()
    response = client.get("/connect")
    assert response.status_code == 200
    assert b'type="password"' in response.data
    assert b'credentials are encrypted' in response.data
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_linq_replies_to_originating_chat(monkeypatch):
    post = MagicMock()
    monkeypatch.setattr("shopper.linq.requests.post", post)
    LinqClient("test", "https://api.example/v3").send("chat-a", "Connect here")
    assert post.call_args.args[0] == "https://api.example/v3/chats/chat-a/messages"
    assert post.call_args.kwargs["json"] == {"message": {"parts": [{"type": "text", "value": "Connect here"}]}}
    assert "timeout" in post.call_args.kwargs


def page_mock(url="https://www.doordash.com/consumer/edit_profile/"):
    page = MagicMock()
    page.url = url
    page.locator.return_value.count.return_value = 0
    page.get_by_role.return_value.count.return_value = 0
    page.evaluate.return_value = False
    return page


def test_login_requires_positive_evidence():
    page = page_mock("https://www.doordash.com/")
    assert not signed_in(page)
    page.url = "https://www.doordash.com/consumer/login/"
    page.evaluate.return_value = True
    assert not signed_in(page)
    page.url = "https://www.doordash.com/consumer/edit_profile/"
    assert signed_in(page)
    page.locator.return_value.count.return_value = 1  # Password or MFA form still visible.
    assert not signed_in(page)
    assert not is_doordash("https://doordash.com.evil.example")


def test_wait_submits_credentials_then_confirms_login(monkeypatch):
    page = page_mock("https://www.doordash.com/consumer/login/")
    job = LoginJob("alice", "chat", "")
    job.begin_credentials(60)
    job.submit_sign_in("user@example.test", "secret", lambda: None)
    fill = MagicMock(side_effect=lambda current_page, *_: setattr(current_page, "url", "https://www.doordash.com/consumer/edit_profile/"))
    monkeypatch.setattr("shopper.browser.fill_doordash_credentials", fill)
    monkeypatch.setattr("shopper.browser.login_fields", lambda current_page: (MagicMock(), MagicMock()))
    monkeypatch.setattr("shopper.browser.signed_in", lambda current_page: current_page.url.endswith("edit_profile/"))
    ticks = iter(range(0, 1000, 3))
    wait_for_login(page, job, clock=lambda: next(ticks))
    fill.assert_called_once_with(page, "user@example.test", "secret")


def test_wait_times_out_or_cancels_without_navigating():
    page = page_mock()
    job = LoginJob("alice", "chat", "")
    job.begin_credentials(60)
    job.deadline = 1
    with pytest.raises(LoginExpired):
        wait_for_login(page, job, clock=lambda: 2)
    job.deadline = 100
    job.cancelled.set()
    with pytest.raises(LoginCancelled):
        wait_for_login(page, job, clock=lambda: 2)
    page.goto.assert_not_called()


def test_runner_keeps_same_session_and_releases_after_continuation(config, monkeypatch):
    store = memory_store(config.credential_encryption_key)
    bb = MagicMock()
    bb.contexts.create.return_value.id = "context-a"
    bb.sessions.create.return_value = SimpleNamespace(id="session-a", connect_url="wss://example.test")
    page = page_mock()
    browser = MagicMock()
    browser.contexts = [SimpleNamespace(pages=[page])]
    playwright = MagicMock()
    playwright.__enter__.return_value.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr("shopper.browser.sync_playwright", lambda: playwright)
    monkeypatch.setattr("shopper.browser.wait_for_login", lambda p, job: None)
    job = LoginJob("alice", "chat", config.public_base_url)
    notify = MagicMock()
    callback = MagicMock()
    DoorDashBrowser(config, store, client=bb).run(job, notify, after_login=callback)
    callback.assert_called_once_with("session-a", page)
    assert "#" + job.token in notify.call_args.args[0]
    assert job.state == "resuming"
    assert bb.sessions.create.call_args.kwargs["browser_settings"]["context"] == {"id": "context-a", "persist": True}
    assert bb.sessions.create.call_args.kwargs["browser_settings"]["record_session"] is False
    bb.sessions.update.assert_called_once_with("session-a", status="REQUEST_RELEASE")
    browser.close.assert_called_once()
    assert store.context_for("alice")[1] < time.time() + 6


def test_runner_uses_saved_doordash_credentials(config, monkeypatch):
    store = memory_store(config.credential_encryption_key)
    store.save_context("alice", "context-a")
    store.save_credentials("alice", "user@example.test", "encrypted-password")
    bb = MagicMock()
    bb.sessions.create.return_value = SimpleNamespace(id="session-a", connect_url="wss://example.test")
    page = page_mock()
    browser = MagicMock()
    browser.contexts = [SimpleNamespace(pages=[page])]
    playwright = MagicMock()
    playwright.__enter__.return_value.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr("shopper.browser.sync_playwright", lambda: playwright)
    fill = MagicMock()
    monkeypatch.setattr("shopper.browser.fill_doordash_credentials", fill)
    wait = MagicMock()
    monkeypatch.setattr("shopper.browser.wait_for_login", wait)
    job = LoginJob("alice", "chat", config.public_base_url)

    DoorDashBrowser(config, store, client=bb).run(job, MagicMock())

    fill.assert_called_once_with(page, "user@example.test", "encrypted-password")
    wait.assert_called_once_with(page, job)
    assert job.state == "resuming"
    bb.sessions.update.assert_called_once_with("session-a", status="REQUEST_RELEASE")


@pytest.mark.parametrize("exception", [LoginExpired, LoginCancelled, RuntimeError])
def test_runner_releases_browser_on_handoff_failure(config, monkeypatch, exception):
    bb = MagicMock()
    bb.contexts.create.return_value.id = "context-a"
    bb.sessions.create.return_value = SimpleNamespace(id="session-a", connect_url="wss://example.test")
    page = page_mock()
    browser = MagicMock()
    browser.contexts = [SimpleNamespace(pages=[page])]
    playwright = MagicMock()
    playwright.__enter__.return_value.chromium.connect_over_cdp.return_value = browser
    monkeypatch.setattr("shopper.browser.sync_playwright", lambda: playwright)
    def fail(*args):
        raise exception()
    monkeypatch.setattr("shopper.browser.wait_for_login", fail)
    with pytest.raises(exception):
        DoorDashBrowser(config, memory_store(config.credential_encryption_key), client=bb).run(LoginJob("alice", "chat", ""), lambda _: None)
    bb.sessions.update.assert_called_once()
    browser.close.assert_called_once()


def test_concurrent_connects_share_one_active_browser(config):
    messenger = MagicMock()
    started = threading.Event()
    def run(job, notify):
        job.begin_credentials(60)
        started.set()
        job.cancelled.wait(5)
        raise LoginCancelled()
    runner = SimpleNamespace(run=run)
    service = ConnectionService(config, memory_store(config.credential_encryption_key), messenger, runner)
    try:
        first = service.start("alice", "chat-a")
        assert started.wait(2)
        assert service.start("alice", "chat-a") is None
        assert len(service.jobs) == 1
        assert service.lookup(first.token) is first
        assert service.lookup("wrong-user-token") is None
        assert first.link in messenger.send.call_args.args[1]
    finally:
        service.close()


def test_complete_service_sends_link_then_confirmation(config):
    messenger = MagicMock()
    complete = threading.Event()
    def run(job, notify):
        job.begin_credentials(60)
        notify(job.link)
        job.transition("resuming", "Connecting")
        complete.set()
    service = ConnectionService(config, memory_store(config.credential_encryption_key), messenger, SimpleNamespace(run=run))
    try:
        job = service.start("alice", "chat-a")
        assert complete.wait(2)
        service.browser_pool.shutdown(wait=True)
        assert job.state == "connected"
        assert messenger.send.call_count == 2
        assert messenger.send.call_args.args[0] == "chat-a"
        assert not job.enqueue("key", "a")
    finally:
        service.close()

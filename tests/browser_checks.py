"""Opt-in frontend checks: python -m pytest tests/browser_checks.py -q.

Uses local Chrome and a local Flask fixture; never contacts DoorDash or Browserbase.
"""
import logging
import threading
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
import mongomock
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from shopper.app import create_app
from shopper.config import Settings
from shopper.service import LoginJob
from shopper.store import Store


@pytest.fixture
def fixture_app(tmp_path):
    config = Settings(public_base_url="https://fixture.example",
                      credential_encryption_key=Fernet.generate_key().decode())
    store = Store(config.mongo_uri, config.credential_encryption_key, client=mongomock.MongoClient())
    job = LoginJob("fixture-user", "fixture-chat", config.public_base_url)
    job.begin_credentials(600)
    service = MagicMock()
    service.lookup.side_effect = lambda token: job if token == job.token else None
    app = create_app(config, service=service, store=store)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    origin = f"http://127.0.0.1:{server.server_port}"
    object.__setattr__(config, "public_base_url", origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield origin, job, store
    server.shutdown()
    thread.join()


@pytest.fixture
def chrome():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        yield browser
        browser.close()


def test_mobile_credential_form_submits_and_clears_secrets(chrome, fixture_app):
    origin, job, store = fixture_app
    context = chrome.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(origin + "/connect#" + job.token)
    expect(page.locator("#credentials-form")).to_be_visible()
    assert "#" not in page.url
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_label("Email or phone").fill("fixture@example.test")
    page.get_by_label("Password").fill("fixture-secret")
    page.get_by_role("button", name="Save and sign in").click()
    expect(page.locator("#empty-text")).to_contain_text("Signing in", timeout=5000)
    assert store.credentials_for("fixture-user") == ("fixture@example.test", "fixture-secret")
    assert page.locator("#password").input_value() == ""
    action, details = job.commands.get(timeout=2)
    assert action == "credentials"
    assert details == {"login": "fixture@example.test", "password": "fixture-secret"}
    details.clear()
    job.transition("awaiting_code", "Enter the verification code DoorDash sent you.")
    expect(page.locator("#code-form")).to_be_visible(timeout=5000)
    page.get_by_label("Verification code").fill("123456")
    page.get_by_role("button", name="Verify code").click()
    action, code = job.commands.get(timeout=2)
    assert (action, code) == ("code", "123456")
    assert not errors
    context.close()


def test_connection_link_expires_without_a_valid_token(chrome, fixture_app):
    origin, _, _ = fixture_app
    page = chrome.new_page()
    page.goto(origin + "/connect#invalid-token")
    expect(page.locator("#empty-text")).to_contain_text("expired")
    page.context.close()

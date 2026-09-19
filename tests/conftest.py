"""Tests must replace provider calls explicitly; never spend credits or send messages."""

import pytest
import requests


@pytest.fixture(autouse=True)
def block_unmocked_http(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Unmocked HTTP request: tests must use provider fixtures.")

    monkeypatch.setattr(requests.Session, "request", blocked)

import json
from unittest.mock import MagicMock

import pytest

from shopper.fetch_chat import FetchChatError, run_fetch_chat


def test_run_fetch_chat_normalizes_updates(monkeypatch):
    post = MagicMock()
    post.return_value.raise_for_status.return_value = None
    post.return_value.json.return_value = {
        "status": "completed",
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": json.dumps({
                    "reply": "Added it for you.",
                    "selected_date": "2026-09-28",
                    "calendar_updates": [
                        {"action": "upsert", "date": "2026-09-28", "note": "Salmon bowl"},
                        {"action": "remove", "date": "2026-09-29", "note": None},
                        {"action": "upsert", "date": "bad-date", "note": "Ignore me"},
                    ],
                    "preference_updates": [
                        {"action": "add", "list": "favouriteFoods", "value": "Salmon bowls"},
                        {"action": "remove", "list": "avoidFoods", "value": "Olives"},
                        {"action": "toggle", "list": "dietaryRestrictions", "value": "Vegan"},
                        {"action": "add", "list": "invalidList", "value": "Ignore me"},
                    ],
                })
            }]
        }]
    }
    monkeypatch.setattr("shopper.fetch_chat.requests.post", post)

    result = run_fetch_chat(
        api_key="openai-test",
        model="gpt-4.1-mini",
        message="Add salmon bowl to September 28",
        conversation=[{"role": "user", "text": "Hi"}],
        state={"selectedDate": "2026-09-19", "exportedAt": "2026-09-19T12:00:00Z"},
    )

    assert result == {
        "reply": "Added it for you.",
        "selected_date": "2026-09-28",
        "calendar_updates": [
            {"action": "upsert", "date": "2026-09-28", "note": "Salmon bowl"},
            {"action": "remove", "date": "2026-09-29", "note": ""},
        ],
        "preference_updates": [
            {"action": "add", "list": "favouriteFoods", "value": "Salmon bowls"},
            {"action": "remove", "list": "avoidFoods", "value": "Olives"},
            {"action": "toggle", "list": "dietaryRestrictions", "value": "Vegan"},
        ],
    }
    assert post.call_args.args[0] == "https://api.openai.com/v1/responses"
    assert post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer openai-test",
        "Content-Type": "application/json",
    }


def test_run_fetch_chat_requires_api_key():
    with pytest.raises(FetchChatError, match="OPENAI_API_KEY"):
        run_fetch_chat(api_key="", model="gpt-4.1-mini", message="Hi", conversation=[], state={})
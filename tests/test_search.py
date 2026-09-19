import json
import os
import threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
import requests

from shopper.app import create_app
from shopper.config import Settings
from shopper.linq import IncomingMessage
from shopper.search import (DEMO_SEARCH_QUERY, PAGE_CHAR_LIMIT, BrowserbaseWeb, GrocerySearch,
                            ProductStandardizer, SearchCancelled, SearchError, SearchReport, Source)
from shopper.service import SearchService
from test_flow import SECRET, headers, payload

CONTENT = "Example Unsweetened Oat Milk 1L. Listed price $4.29 CAD. Available online in Canada."
SETTINGS = Settings(browserbase_api_key="bb-test", openai_api_key="openai-test",
                    demo_user_handle="+15550000001", linq_webhook_secret=SECRET)


def offer_data(**overrides):
    return {"source_id": "1", "product_type": "oat_milk",
            "product_quote": "Example Unsweetened Oat Milk 1L", "variant": "unsweetened",
            "variant_quote": "Unsweetened", "size_quote": "Oat Milk 1L",
            "price_quote": "$4.29 CAD", "availability_quote": "Available online in Canada",
            **overrides}


def ai_response(offers=None):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"offers": [offer_data()] if offers is None else offers})}]}]}


@pytest.fixture
def api(monkeypatch):
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/search"):
            data = {"results": [{"url": f"https://shop.example/product/{i}", "title": f"Oat milk {i}"}
                                for i in range(12)]}
        elif url.endswith("/fetch"):
            data = {"statusCode": 200, "content": CONTENT}
        elif url.endswith("/responses"):
            data = ai_response()
        else:
            pytest.fail(f"Unexpected network call: {url}")
        response = MagicMock()
        response.json.return_value = data
        return response

    monkeypatch.setattr("shopper.search.requests.post", post)
    return calls


def test_search_then_fetch_then_rank_uses_real_provider_contracts(api):
    report = GrocerySearch(SETTINGS).run()
    assert report.found == report.fetched == 10
    assert len(report.choices) == 1
    assert "https://shop.example/product/0" in report.as_text()
    assert "Listed price: $4.29 CAD ($4.29/L)" in report.as_text()
    assert api[0][0] == "https://api.browserbase.com/v1/search"
    assert api[0][1]["json"] == {"query": DEMO_SEARCH_QUERY, "numResults": 10}
    assert api[0][1]["headers"] == {"X-BB-API-Key": "bb-test"}
    assert len(api) == 12
    for url, args in api[1:-1]:
        assert url == "https://api.browserbase.com/v1/fetch"
        assert args["json"]["format"] == "markdown"
        assert args["json"]["allowRedirects"] is True
    url, args = api[-1]
    assert url == "https://api.openai.com/v1/responses"
    assert args["headers"] == {"Authorization": "Bearer openai-test"}
    body = args["json"]
    assert body["store"] is False
    assert body["text"]["format"]["strict"] is True
    assert body["model"] == "gpt-4.1-mini"
    evidence = json.loads(body["input"][1]["content"])
    assert len(evidence["sources"]) == 10
    assert all(s["content"] == CONTENT for s in evidence["sources"])
    assert body["text"]["format"]["name"] == "normalized_offers"
    assert "do not rank products" in body["input"][0]["content"]
    assert "openai-test" not in json.dumps(evidence)
    assert all(args["timeout"][0] == 5 for _, args in api)


def test_search_deduplicates_and_rejects_non_public_urls(monkeypatch):
    urls = ["https://shop.example/milk#top", "https://shop.example/milk#bottom", "file:///etc/passwd",
            "http://127.0.0.1/admin", "http://localhost/a", "https://user:pass@shop.example/a",
            "http://192.168.1.1/", "https://shop.local/a", "https://shop.example:1234/a"]
    monkeypatch.setattr("shopper.search.post_json", lambda *a, **k: {
        "results": [{"url": url, "title": "Milk"} for url in urls]})
    assert BrowserbaseWeb("key").search("milk") == [Source("1", "Milk", "https://shop.example/milk")]


def test_fetch_checks_target_status_and_caps_content(monkeypatch):
    source = Source("1", "Milk", "https://shop.example/milk")
    mock = MagicMock(return_value={"statusCode": 200, "content": "x" * (PAGE_CHAR_LIMIT + 100)})
    monkeypatch.setattr("shopper.search.post_json", mock)
    assert len(BrowserbaseWeb("key").fetch(source).content) == PAGE_CHAR_LIMIT
    for data in ({"statusCode": 403, "content": "Access denied"}, {"statusCode": 200, "content": ""},
                 {"statusCode": 200, "content": {"html": "unexpected"}}):
        mock.return_value = data
        with pytest.raises(SearchError):
            BrowserbaseWeb("key").fetch(source)


def test_partial_fetch_failure_only_ranks_readable_pages(api):
    search = GrocerySearch(SETTINGS)
    original = search.web.fetch
    def fetch(source):
        if source.id == "1":
            return original(source)
        raise SearchError("Page unavailable")

    search.web.fetch = fetch
    report = search.run()
    assert report.found == 10 and report.fetched == 1
    assert "Read 1 of 10" in report.as_text()
    assert len(json.loads(api[-1][1]["json"]["input"][1]["content"])["sources"]) == 1


def test_empty_search_and_failed_fetches_do_not_call_ai(api):
    search = GrocerySearch(SETTINGS)
    search.standardizer = MagicMock()
    search.web.search = MagicMock(return_value=[])
    assert search.run().choices == []
    search.web.search.return_value = [Source("1", "Milk", "https://shop.example/milk")]
    search.web.fetch = MagicMock(side_effect=SearchError("unreadable"))
    with pytest.raises(SearchError, match="couldn't read"):
        search.run()
    search.standardizer.standardize_and_rank.assert_not_called()


@pytest.mark.parametrize("offers", [
    [offer_data(source_id="invented")],
    [offer_data(), offer_data()],
    [offer_data(price_quote="$0.01 CAD")],
    [offer_data(product_quote="Organic certified")],
    [offer_data(variant_quote="Sweetened")],
    [offer_data(extra="unexpected")],
])
def test_rejects_ungrounded_ai_output(monkeypatch, offers):
    monkeypatch.setattr("shopper.search.post_json", lambda *a, **k: ai_response(offers))
    with pytest.raises(SearchError, match="couldn't verify"):
        ProductStandardizer("key", "model").standardize_and_rank(
            DEMO_SEARCH_QUERY, [Source("1", "Milk", "https://shop.example/milk", CONTENT)])


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "bad json"}]}]},
])
def test_handles_refusal_and_incomplete_ai_response(monkeypatch, response):
    monkeypatch.setattr("shopper.search.post_json", lambda *a, **k: response)
    with pytest.raises(SearchError):
        ProductStandardizer("key", "model").standardize_and_rank(
            DEMO_SEARCH_QUERY, [Source("1", "Milk", "https://shop.example/milk", CONTENT)])


def test_unknown_price_is_explicit_and_empty_ranking_is_valid(monkeypatch):
    source = Source("1", "Milk", "https://shop.example/milk", CONTENT)
    ranker = ProductStandardizer("key", "model")
    monkeypatch.setattr("shopper.search.post_json", lambda *a, **k: ai_response([offer_data(price_quote=None)]))
    choices = ranker.standardize_and_rank(DEMO_SEARCH_QUERY, [source])
    assert "Listed price: not confirmed" in SearchReport(DEMO_SEARCH_QUERY, 1, 1, choices).as_text()
    monkeypatch.setattr("shopper.search.post_json", lambda *a, **k: ai_response([]))
    assert ranker.standardize_and_rank(DEMO_SEARCH_QUERY, [source]) == []


@pytest.mark.parametrize("stage", ["search", "fetch", "rank"])
def test_cancellation_prevents_later_steps(api, stage):
    search, cancelled = GrocerySearch(SETTINGS), threading.Event()
    target = search.standardizer if stage == "rank" else search.web
    method = "standardize_and_rank" if stage == "rank" else stage
    original = getattr(target, method)

    def cancel_after(*args):
        result = original(*args)
        cancelled.set()
        return result

    setattr(target, method, cancel_after)
    with pytest.raises(SearchCancelled):
        search.run(cancelled=cancelled)
    if stage != "rank":
        assert not any(url.endswith("/responses") for url, _ in api)
    if stage == "search":
        assert len(api) == 1


def test_missing_openai_key_fails_before_any_paid_request(api):
    with pytest.raises(SearchError, match="OPENAI_API_KEY"):
        GrocerySearch(replace(SETTINGS, openai_api_key="")).run()
    assert api == []


def test_search_environment_only_requires_search_and_messaging_keys(monkeypatch):
    monkeypatch.setattr("shopper.config.load_dotenv", lambda: None)
    with patch.dict(os.environ, {"BROWSERBASE_API_KEY": "bb-test", "OPENAI_API_KEY": "ai-test",
                                "LINQ_API_KEY": "linq-test", "LINQ_WEBHOOK_SECRET": SECRET,
                                "DEMO_USER_HANDLE": "+15550000001"}, clear=True):
        settings = Settings.from_env()
    assert settings.openai_api_key == "ai-test"


def test_provider_failure_is_sanitized(monkeypatch):
    response = requests.Response()
    response.status_code = 403
    monkeypatch.setattr("shopper.search.requests.post", MagicMock(side_effect=requests.HTTPError(
        "secret-key provider response", response=response)))
    with pytest.raises(SearchError, match="API access") as exc:
        GrocerySearch(SETTINGS).run()
    assert "secret-key" not in str(exc.value)


@pytest.mark.parametrize("query", ["", "   ", "x" * 201, None])
def test_invalid_query_never_calls_providers(api, query):
    with pytest.raises(SearchError, match="between 1 and 200"):
        GrocerySearch(SETTINGS).run(query)
    assert not api


@pytest.mark.parametrize("failure", [requests.Timeout("secret"), requests.ConnectionError("secret")])
def test_network_failures_become_retryable_user_messages(monkeypatch, failure):
    monkeypatch.setattr("shopper.search.requests.post", MagicMock(side_effect=failure))
    with pytest.raises(SearchError, match="try again") as exc:
        GrocerySearch(SETTINGS).run()
    assert "secret" not in str(exc.value)


def test_cli_runs_pipeline_and_returns_failure_exit_code(api, monkeypatch, capsys):
    import searchtest

    monkeypatch.setattr(searchtest.Settings, "from_env", lambda **kwargs: SETTINGS)
    monkeypatch.setattr("sys.argv", ["searchtest.py", "unsweetened oat milk Canada"])
    assert searchtest.main() == 0
    output = capsys.readouterr()
    assert "Best matches" in output.out
    assert "Searching the web" in output.err
    assert api[0][1]["json"]["query"] == "unsweetened oat milk Canada"
    api.clear()
    monkeypatch.setattr(searchtest.Settings, "from_env", lambda **kwargs: replace(SETTINGS, openai_api_key=""))
    assert searchtest.main() == 1
    output = capsys.readouterr()
    assert "OPENAI_API_KEY" in output.err
    assert not output.out and not api


def dispatch(service, text, *, chat="chat-a", handle=SETTINGS.demo_user_handle):
    assert service.message_slots.acquire(blocking=False)
    service._handle(IncomingMessage("event", chat, "user", text, handle))


def test_signed_search_webhook_runs_without_login_and_deduplicates(api):
    messenger, sent = MagicMock(), threading.Event()
    messenger.send.side_effect = lambda chat, text: sent.set() if "Best matches" in text else None
    service = SearchService(SETTINGS, messenger)
    try:
        client = create_app(SETTINGS, service=service).test_client()
        body = json.dumps(payload(text=" SEARCH ")).encode()
        assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
        assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
        assert sent.wait(3)
        assert service.search_job.finished.wait(3)
        assert sum(url.endswith("/search") for url, _ in api) == 1
        assert all(call.args[0] == "chat-a" for call in messenger.send.call_args_list)
    finally:
        service.close()


def test_active_search_handles_duplicate_status_cancel_and_chat_isolation():
    entered, release = threading.Event(), threading.Event()
    searcher, messenger = MagicMock(), MagicMock()

    def run(query, *, cancelled, progress):
        assert query == DEMO_SEARCH_QUERY
        progress("Reading search results…")
        entered.set()
        assert release.wait(3)
        return SearchReport(query, 1, 1, [])

    searcher.run.side_effect = run
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        dispatch(service, "SEARCH", handle="+15550000002")
        assert service.search_job is None
        messenger.send.assert_not_called()
        dispatch(service, "SEARCH")
        assert entered.wait(3)
        dispatch(service, "SEARCH")
        assert searcher.run.call_count == 1
        dispatch(service, "STATUS")
        assert messenger.send.call_args.args == ("chat-a", "Reading search results…")
        dispatch(service, "CANCEL", chat="chat-b")
        assert not service.search_job.cancelled.is_set()
        dispatch(service, "CANCEL")
        assert service.search_job.cancelled.is_set()
        release.set()
        assert service.search_job.finished.wait(3)
        assert not any("Read 1 of 1" in call.args[1] for call in messenger.send.call_args_list)
        dispatch(service, "STATUS")
        assert "cancelled" in messenger.send.call_args.args[1]
    finally:
        release.set()
        service.close()


def test_concurrent_starts_share_one_search_and_close_cancels_it():
    entered = threading.Event()
    searcher, messenger = MagicMock(), MagicMock()

    def run(query, *, cancelled, progress):
        entered.set()
        assert cancelled.wait(3)
        raise SearchCancelled()

    searcher.run.side_effect = run
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(lambda _: service.start_search("chat-a"), range(8)))
        assert entered.wait(3)
        assert sum(job is not None for job in jobs) == 1
        assert searcher.run.call_count == 1
    finally:
        service.close()
    assert service.search_job.finished.is_set()
    assert service.start_search("chat-a") is None


def test_failed_search_releases_worker_and_allows_retry():
    searcher, messenger = MagicMock(), MagicMock()
    searcher.run.side_effect = [SearchError("Browserbase Search is unavailable."), SearchReport("milk", 0, 0, [])]
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        first = service.start_search("chat-a")
        assert first.finished.wait(3)
        assert "unavailable" in first.message
        second = service.start_search("chat-a")
        assert second is not first and second.finished.wait(3)
        assert "Search complete" in second.message
        assert searcher.run.call_count == 2
    finally:
        service.close()


def test_missing_search_configuration_sends_setup_reply_without_job(api):
    messenger = MagicMock()
    service = SearchService(replace(SETTINGS, openai_api_key=""), messenger)
    try:
        dispatch(service, "SEARCH")
        assert service.search_job is None
        assert "OPENAI_API_KEY" in messenger.send.call_args.args[1]
        assert not api
    finally:
        service.close()

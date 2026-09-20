import json
import threading
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest

from shopper.search import (BrowserbaseWeb, GrocerySearch, SearchCancelled, SearchError,
                            Source, supported_url, targeted_query)
from test_search import CONTENT, SETTINGS, TEST_QUERY, ai_response


def result(url):
    return {"url": url, "title": "Unsweetened Oat Milk 1L"}


@pytest.mark.parametrize("domain", [
    "doordash.com", "ubereats.com", "skipthedishes.com", "instacart.ca",
    "instacart.com", "walmart.ca", "walmart.com",
])
def test_supported_roots_and_subdomains(domain):
    assert supported_url(f"https://{domain}/milk")
    assert supported_url(f"https://www.{domain}/milk")


@pytest.mark.parametrize("url", [
    "https://walmart.ca.evil.example/milk", "https://fakewalmart.ca/milk",
    "https://evil.example/?store=doordash.com", "https://evil.example/walmart.ca",
    "https://walmart.ca@evil.example/milk", "https://evil.example@walmart.ca/milk",
    "http://127.0.0.1/walmart.ca", "https://unapproved.example/milk",
    "https://www.walmart.ca:8443/milk", "file:///walmart.ca",
])
def test_unsupported_and_deceptive_urls_are_never_fetched(monkeypatch, url):
    post = MagicMock()
    monkeypatch.setattr("shopper.search.post_json", post)
    assert supported_url(url) is None
    with pytest.raises(SearchError, match="supported store"):
        BrowserbaseWeb("key").fetch(Source("1", "Milk", url))
    post.assert_not_called()


@pytest.fixture
def discovery(monkeypatch):
    batches, calls = [], []

    def post(url, **kwargs):
        calls.append((url, kwargs["payload"]))
        if url.endswith("/search"):
            batch = batches.pop(0)
            if isinstance(batch, Exception):
                raise batch
            return {"results": batch}
        if url.endswith("/fetch"):
            return {"statusCode": 200, "content": CONTENT}
        if url.endswith("/responses"):
            return ai_response()
        pytest.fail("Unexpected provider")

    monkeypatch.setattr("shopper.search.post_json", post)
    return batches, calls


def test_second_search_filters_deduplicates_and_caps_total_fetches(discovery):
    batches, calls = discovery
    batches.extend([
        [result("https://www.doordash.com/milk#first")]
        + [result(f"https://unapproved.example/{i}") for i in range(24)],
        [result("https://www.doordash.com/milk#again")]
        + [result(f"https://www.walmart.ca/milk/{i}") for i in range(24)],
    ])
    report = GrocerySearch(SETTINGS).run(TEST_QUERY)
    searches = [p for url, p in calls if url.endswith("/search")]
    fetches = [p for url, p in calls if url.endswith("/fetch")]
    extractions = [p for url, p in calls if url.endswith("/responses")]

    assert len(searches) == 2
    assert all(p["numResults"] == 25 for p in searches)
    assert searches[0]["query"] != searches[1]["query"]
    assert len(fetches) == 10
    assert len({p["url"] for p in fetches}) == 10
    assert {urlsplit(p["url"]).hostname for p in fetches} == {"www.doordash.com", "www.walmart.ca"}
    assert len(extractions) == 1
    evidence = json.loads(extractions[0]["input"][1]["content"])
    assert [s["id"] for s in evidence["sources"]] == [str(i) for i in range(1, 11)]
    assert report.found == 25 and report.fetched == 10


@pytest.mark.parametrize("count,search_count", [(9, 2), (10, 1), (25, 1)])
def test_second_search_only_when_fewer_than_ten_approved_results(discovery, count, search_count):
    batches, calls = discovery
    batches.extend([[result(f"https://www.walmart.ca/{i}") for i in range(count)], []])
    GrocerySearch(SETTINGS).run(TEST_QUERY)
    assert sum(url.endswith("/search") for url, _ in calls) == search_count
    assert sum(url.endswith("/fetch") for url, _ in calls) == min(count, 10)


def test_no_supported_results_stops_after_two_searches_without_fetch_or_ai(discovery):
    batches, calls = discovery
    # A supported URL beyond the requested 25 must not evade the discovery cap.
    batch = [result(f"https://unapproved.example/{i}") for i in range(25)]
    batches.extend([batch + [result("https://www.walmart.ca/milk")], batch])
    report = GrocerySearch(SETTINGS).run(TEST_QUERY)
    assert len(calls) == 2 and all(url.endswith("/search") for url, _ in calls)
    assert report.found == report.fetched == 0
    assert "No matching results found on DoorDash" in report.as_text()


def test_failed_second_search_preserves_first_results(discovery):
    batches, calls = discovery
    batches.extend([[result("https://www.walmart.ca/milk")], SearchError("unavailable")])
    report = GrocerySearch(SETTINGS).run(TEST_QUERY)
    assert report.found == report.fetched == 1
    assert len(report.choices) == 1
    assert sum(url.endswith("/search") for url, _ in calls) == 2


def test_failed_second_search_with_no_results_reports_failure(discovery):
    batches, calls = discovery
    batches.extend([[], SearchError("unavailable")])
    with pytest.raises(SearchError, match="unavailable"):
        GrocerySearch(SETTINGS).run(TEST_QUERY)
    assert len(calls) == 2


@pytest.mark.parametrize("cancel_on_call", [1, 2])
def test_cancellation_stops_expansion_and_fetching(monkeypatch, cancel_on_call):
    cancelled = threading.Event()
    search = GrocerySearch(SETTINGS)

    def search_web(query):
        if search.web.search.call_count == cancel_on_call:
            cancelled.set()
        return []

    search.web.search = MagicMock(side_effect=search_web)
    search.web.fetch = MagicMock()
    with pytest.raises(SearchCancelled):
        search.run(cancelled=cancelled)
    assert search.web.search.call_count == cancel_on_call
    search.web.fetch.assert_not_called()


def test_expanded_query_fits_api_limit_and_ranking_keeps_original(discovery):
    batches, calls = discovery
    query = "unsweetened oat milk "
    query += "x" * (200 - len(query))
    batches.extend([[], [result("https://www.walmart.ca/milk")]])
    report = GrocerySearch(SETTINGS).run(query)
    searches = [p for url, p in calls if url.endswith("/search")]
    assert all(1 <= len(p["query"]) <= 200 for p in searches)
    assert searches[1]["query"] == targeted_query(query)
    assert "site:doordash.com" in searches[1]["query"]
    assert "site:walmart.ca" in searches[1]["query"]
    assert report.query == query
    ai_payload = next(p for url, p in calls if url.endswith("/responses"))
    assert json.loads(ai_payload["input"][1]["content"])["query"] == query


def test_redirect_cannot_leave_supported_domains(monkeypatch):
    post = MagicMock(return_value={
        "statusCode": 302, "headers": {"location": "https://unapproved.example/milk"},
        "content": CONTENT,
    })
    monkeypatch.setattr("shopper.search.post_json", post)
    with pytest.raises(SearchError):
        BrowserbaseWeb("key").fetch(Source("1", "Milk", "https://www.walmart.ca/milk"))
    assert post.call_args.kwargs["payload"]["allowRedirects"] is False
    post.assert_called_once()

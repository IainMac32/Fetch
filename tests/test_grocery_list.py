import json
import threading
from urllib.parse import urlsplit
from unittest.mock import MagicMock

import pytest

from shopper.app import create_app
from shopper.grocery_list import (MAX_LIST_ITEMS, parse_grocery_list, search_grocery_list)
from shopper.search import (SUPPORTED_PLATFORMS, GrocerySearch, SearchCancelled,
                            SearchError, SearchReport)
from shopper.service import SearchService
from test_flow import headers, payload
from test_search import SETTINGS, dispatch


@pytest.mark.parametrize("text,expected", [
    ("apples, bananas, oranges", ("apples", "bananas", "oranges")),
    ("6 apples; 2 bananas\n500 g strawberries", ("6 apples", "2 bananas", "500 g strawberries")),
    ("1. 6 apples\n2) 2 bananas\n• 1 L milk\n- bread\n* eggs",
     ("6 apples", "2 bananas", "1 L milk", "bread", "eggs")),
    ("mac and cheese, salt & vinegar chips", ("mac and cheese", "salt & vinegar chips")),
    ("1,5 L milk, bananas", ("1,5 L milk", "bananas")),
    ("apples,,;\n bananas,", ("apples", "bananas")),
    ("Apples, APPLES, 2 apples, 3 apples", ("Apples", "2 apples", "3 apples")),
    ("  1.5 kg   apples \r\n  bananas ", ("1.5 kg apples", "bananas")),
])
def test_list_formats_preserve_items_and_quantities(text, expected):
    assert parse_grocery_list(text) == expected


@pytest.mark.parametrize("text", [None, "", " ; ,\n", "x" * 201, "x" * 2001,
                                  ",".join(f"item {i}" for i in range(MAX_LIST_ITEMS + 1))])
def test_invalid_list_never_starts_a_search(text):
    searcher, messenger = MagicMock(), MagicMock()
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        assert service.start_search("chat-a", text) is None
        assert service.search_job is None
        searcher.check_config.assert_not_called()
        searcher.run.assert_not_called()
        messenger.send.assert_called_once()
    finally:
        service.close()


@pytest.mark.parametrize("items", [(), ("apples", "x" * 201), ("apples", "bananas, oranges"),
                                   ("apples", None), ("apples",) * (MAX_LIST_ITEMS + 1)])
def test_runner_validates_entire_list_before_any_search(items):
    searcher = MagicMock()
    with pytest.raises(SearchError):
        search_grocery_list(searcher, items)
    searcher.run.assert_not_called()


@pytest.fixture
def fruit_api(monkeypatch):
    """Exercise the real search/extraction/ranking flow with offline provider replies."""
    calls = []

    def post(url, **kwargs):
        data = kwargs["payload"]
        calls.append((url, data))
        if url.endswith("/search"):
            query, _, domain = data["query"].partition(" site:")
            domain = domain.split()[0]
            fruit = query.split()[-1].lower()
            return {"results": [
                {"title": f"{fruit.title()} 1 kg", "url": f"https://www.{domain}/{fruit}/{index}"}
                for index in range(25)
            ]}
        if url.endswith("/fetch"):
            fruit = urlsplit(data["url"]).path.split("/")[1]
            return {"statusCode": 200,
                    "content": f"{fruit.title()} 1 kg. CAD$3.00. Available online."}
        if url.endswith("/responses"):
            evidence = json.loads(data["input"][1]["content"])
            offers = [{"source_id": source["id"], "name_quote": source["title"],
                       "match_type": "direct_product",
                       "attribute_quotes": [], "merchant_quote": "Walmart", "size_quote": "1 kg",
                       "price_quote": "CAD$3.00",
                       "availability_quote": "Available online"} for source in evidence["sources"]]
            return {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"offers": offers})}]}]}
        pytest.fail("Unexpected provider call")

    monkeypatch.setattr("shopper.search.post_json", post)
    return calls


def test_fruits_are_searched_extracted_and_ranked_independently(fruit_api):
    progress = []
    items = parse_grocery_list("6 apples, bananas, oranges")
    report = search_grocery_list(GrocerySearch(SETTINGS), items, progress=progress.append)
    assert report.matched == 3 and report.failed == 0
    searches = [p["query"].split(" site:")[0] for url, p in fruit_api if url.endswith("/search")]
    assert sorted(searches) == sorted(item for item in items for _ in SUPPORTED_PLATFORMS)
    extractions = [json.loads(p["input"][1]["content"]) for url, p in fruit_api if url.endswith("/responses")]
    assert sorted(p["query"] for p in extractions) == sorted(items)
    assert len([url for url, _ in fruit_api if url.endswith("/fetch")]) == 30
    for item, fruit in zip(report.items, ("apples", "bananas", "oranges")):
        assert len(item.report.choices) == 3
        assert all(fruit in offer.name_quote.lower() for offer in item.report.choices)
    text = report.as_text()
    assert "1. 6 apples" in text and "2. bananas" in text and "3. oranges" in text
    assert "Listed price: $3.00 CAD ($3.00/kg)" in text
    assert "No retailer has a complete basket" in text
    assert any(message.startswith("Item 3/3 (oranges): ") for message in progress)


def test_largest_list_keeps_provider_usage_bounded(fruit_api):
    items = tuple(f"fruit{i}" for i in range(MAX_LIST_ITEMS))
    search_grocery_list(GrocerySearch(SETTINGS), items)
    assert sum(url.endswith("/search") for url, _ in fruit_api) == MAX_LIST_ITEMS * len(SUPPORTED_PLATFORMS)
    assert sum(url.endswith("/fetch") for url, _ in fruit_api) == MAX_LIST_ITEMS * 10
    assert sum(url.endswith("/responses") for url, _ in fruit_api) == MAX_LIST_ITEMS


def test_failed_item_and_missing_item_are_distinct_and_do_not_drop_matches(fruit_api):
    searcher = MagicMock()
    orange_report = GrocerySearch(SETTINGS).run_all_stores("oranges")

    def run(query, **kwargs):
        if query == "apples":
            raise SearchError("This page couldn't be read.")
        return orange_report if query == "oranges" else SearchReport(query, 0, 0, [])

    searcher.run_all_stores.side_effect = run
    report = search_grocery_list(searcher, ("apples", "bananas", "oranges"))
    assert report.failed == report.matched == 1
    assert sorted(call.args[0] for call in searcher.run_all_stores.call_args_list) == ["apples", "bananas", "oranges"]
    text = report.as_text()
    assert "Search failed: This page couldn't be read." in text
    assert "No verified matches found." in text
    assert "Oranges" in text


@pytest.mark.parametrize("when", ["before", "during", "after_failure"])
def test_cancel_stops_remaining_items(when):
    cancelled = threading.Event()
    searcher = MagicMock()

    def run(query, **kwargs):
        cancelled.set()
        if when == "after_failure":
            raise SearchError("Unavailable")
        return SearchReport(query, 0, 0, [])

    searcher.run_all_stores.side_effect = run
    if when == "before":
        cancelled.set()
    with pytest.raises(SearchCancelled):
        search_grocery_list(searcher, ("apples", "bananas"), cancelled=cancelled)
    assert searcher.run_all_stores.call_count == (0 if when == "before" else 1)


def test_signed_list_webhook_deduplicates_and_sends_one_grouped_result(fruit_api):
    sent = threading.Event()
    messenger = MagicMock()
    messenger.send.side_effect = lambda chat, text: sent.set() if text.startswith("Recommended site") else None
    service = SearchService(SETTINGS, messenger)
    try:
        client = create_app(SETTINGS, service=service).test_client()
        body = json.dumps(payload(text="SEARCH:\n6 apples\nbananas\noranges")).encode()
        for _ in range(2):
            assert client.post("/linq-webhook", data=body, headers=headers(body)).status_code == 200
        assert sent.wait(3) and service.search_job.finished.wait(3)
        assert service.search_job.items == ("6 apples", "bananas", "oranges")
        assert messenger.send.call_count == 2  # Acknowledgement and one grouped result.
        assert all(call.args[0] == "chat-a" for call in messenger.send.call_args_list)
        assert sum(url.endswith("/search") for url, _ in fruit_api) == 3 * len(SUPPORTED_PLATFORMS)
        assert "matches for 3/3 items" in service.search_job.message
    finally:
        service.close()


def test_active_list_status_cancel_and_duplicate_do_not_start_other_items():
    entered, release = threading.Event(), threading.Event()
    searcher, messenger = MagicMock(), MagicMock()

    def run(query, *, cancelled, progress):
        progress("Reading pages…")
        entered.set()
        assert release.wait(3)
        return SearchReport(query, 0, 0, [])

    searcher.run_all_stores.side_effect = run
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        dispatch(service, "SEARCH apples, bananas")
        assert entered.wait(3)
        dispatch(service, "SEARCH oranges, pears")
        assert service.search_job.items == ("apples", "bananas")
        dispatch(service, "STATUS")
        assert any(label in messenger.send.call_args.args[1]
                   for label in ("Item 1/2 (apples)", "Item 2/2 (bananas)"))
        dispatch(service, "CANCEL")
        release.set()
        assert service.search_job.finished.wait(3)
        assert 1 <= searcher.run_all_stores.call_count <= 2
        assert all(call.args[0] in ("apples", "bananas")
                   for call in searcher.run_all_stores.call_args_list)
        assert not any(call.args[1].startswith("Recommended site") for call in messenger.send.call_args_list)
    finally:
        release.set()
        service.close()


def test_cli_accepts_list(fruit_api, monkeypatch, capsys):
    import searchtest

    monkeypatch.setattr(searchtest.Settings, "from_env", lambda **kwargs: SETTINGS)
    monkeypatch.setattr("sys.argv", ["searchtest.py", "--debug", "apples, bananas, oranges"])
    assert searchtest.main() == 0
    output = capsys.readouterr()
    assert "matches for 3 of 3 items" in output.out
    assert "Item 3/3 (oranges)" in output.err


def test_cli_returns_failure_with_partial_results(monkeypatch, capsys):
    import searchtest

    searcher = MagicMock()
    searcher.run_all_stores.side_effect = [SearchError("Unavailable"), SearchReport("bananas", 0, 0, [])]
    monkeypatch.setattr(searchtest.Settings, "from_env", lambda **kwargs: SETTINGS)
    monkeypatch.setattr(searchtest, "GrocerySearch", lambda settings: searcher)
    monkeypatch.setattr("sys.argv", ["searchtest.py", "apples, bananas"])
    assert searchtest.main() == 1
    output = capsys.readouterr().out
    assert "Search failed: Unavailable" in output
    assert "2. bananas" in output

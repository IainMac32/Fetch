import json
import threading
from unittest.mock import MagicMock

import pytest

from shopper.grocery_list import search_grocery_list
from shopper.search import (GrocerySearch, SearchError, SearchReport, Source,
                            SUPPORTED_PLATFORMS, discovery_query, targeted_query)
from shopper.service import SearchService
from test_search import SETTINGS, TEST_QUERY, api, dispatch

LOCATION = "Toronto, ON M5V 2T6"


def test_single_search_location_is_discovery_only(api):
    report = GrocerySearch(SETTINGS).run(TEST_QUERY, location=LOCATION)
    assert api[0][1]["json"]["query"] == TEST_QUERY + " " + LOCATION
    extraction = json.loads(api[-1][1]["json"]["input"][1]["content"])
    assert extraction["query"] == TEST_QUERY
    assert report.choices
    assert report.location == LOCATION
    assert LOCATION in report.as_text()
    assert "Delivery availability and local prices unverified" in report.as_text()


def test_list_location_reaches_every_store_and_keeps_source_ids_unique():
    searcher = GrocerySearch(SETTINGS)
    searcher.web = MagicMock()

    def search(query, *, domains):
        return [Source(str(i), "Apples", f"https://{domains[0]}/product/{i}") for i in range(2)]

    searcher.web.search.side_effect = search
    searcher.web.fetch.side_effect = lambda source: source
    searcher.standardizer = MagicMock()
    searcher.standardizer.standardize_and_rank.return_value = []
    report = search_grocery_list(searcher, ("2 kg apples", "1 kg bananas"), location=LOCATION)
    queries = {call.args[0] for call in searcher.web.search.call_args_list}
    assert queries == {f"{item} Canada site:{domains[0]} (inurl:product OR inurl:/ip/)"
                       for item in ("2 kg apples", "1 kg bananas")
                       for domains in SUPPORTED_PLATFORMS.values()}
    for call in searcher.standardizer.standardize_and_rank.call_args_list:
        assert call.args[0] in ("2 kg apples", "1 kg bananas")
        assert len({source.id for source in call.args[1]}) == 10
    assert report.location == LOCATION
    assert LOCATION in report.as_text()


def test_fallback_keeps_location_when_no_results():
    searcher = GrocerySearch(SETTINGS)
    searcher.web = MagicMock()
    searcher.web.search.return_value = []
    report = searcher.run("apples", location=LOCATION)
    assert searcher.web.search.call_count == 2
    assert LOCATION in searcher.web.search.call_args_list[0].args[0]
    assert "Canada" in searcher.web.search.call_args_list[1].args[0]
    assert "M5V" not in searcher.web.search.call_args_list[1].args[0]
    assert report.location == LOCATION


@pytest.mark.parametrize("location", ["", " ", "x" * 81, "Toronto\nSEARCH milk", "site:example.com", 123])
def test_invalid_location_fails_before_provider_calls(location):
    searcher = GrocerySearch(SETTINGS)
    searcher.web = MagicMock()
    for run in (searcher.run, searcher.run_all_stores):
        with pytest.raises(SearchError):
            run("apples", location=location)
    with pytest.raises(SearchError):
        search_grocery_list(searcher, ("apples", "bananas"), location=location)
    searcher.web.search.assert_not_called()


def test_long_queries_keep_location_and_site_constraints():
    for query in (discovery_query("apples " * 28, LOCATION),
                  targeted_query("apples " * 28, LOCATION)):
        assert len(query) <= 200
        assert LOCATION in query or "Canada" in query
        assert query.startswith("apples")
    assert "site:walmart.ca" in targeted_query("apples " * 28, LOCATION)


@pytest.mark.parametrize("query", ["apples", "apples, bananas"])
def test_cli_location(monkeypatch, capsys, query):
    import searchtest

    searcher = MagicMock()
    searcher.run.side_effect = lambda query, **kwargs: SearchReport(query, 0, 0, [], location=kwargs["location"])
    searcher.run_all_stores.side_effect = searcher.run.side_effect
    monkeypatch.setattr(searchtest.Settings, "from_env", lambda **kwargs: SETTINGS)
    monkeypatch.setattr(searchtest, "GrocerySearch", lambda settings: searcher)
    monkeypatch.setattr("sys.argv", ["searchtest.py", query, "--location", LOCATION])
    assert searchtest.main() == 0
    calls = searcher.run.call_args_list + searcher.run_all_stores.call_args_list
    assert calls and all(call.kwargs["location"] == LOCATION for call in calls)
    assert LOCATION in capsys.readouterr().out


@pytest.mark.parametrize("query", ["apples", "apples, bananas"])
def test_linq_location_is_per_chat_and_snapshotted_for_running_search(query):
    entered, release = threading.Event(), threading.Event()
    searcher, messenger = MagicMock(), MagicMock()

    def run(query, **kwargs):
        entered.set()
        assert release.wait(3)
        return SearchReport(query, 0, 0, [], location=kwargs.get("location"))

    searcher.run.side_effect = searcher.run_all_stores.side_effect = run
    service = SearchService(SETTINGS, messenger, searcher=searcher)
    try:
        dispatch(service, "LOCATION " + LOCATION)
        dispatch(service, "LOCATION", chat="chat-b")
        assert "No location set" in messenger.send.call_args.args[1]
        dispatch(service, "LOCATION")
        assert LOCATION in messenger.send.call_args.args[1]
        dispatch(service, "LOCATION " + "x" * 81)
        assert service.locations["chat-a"] == LOCATION
        dispatch(service, "SEARCH " + query)
        assert entered.wait(3)
        dispatch(service, "LOCATION Ottawa, ON")
        assert service.search_job.location == LOCATION
        release.set()
        assert service.search_job.finished.wait(3)
        calls = searcher.run.call_args_list + searcher.run_all_stores.call_args_list
        assert all(call.kwargs["location"] == LOCATION for call in calls)
        dispatch(service, "SEARCH pears")
        assert service.search_job.finished.wait(3)
        assert searcher.run.call_args.kwargs["location"] == "Ottawa, ON"
        dispatch(service, "LOCATION CLEAR")
        dispatch(service, "SEARCH pears")
        assert service.search_job.finished.wait(3)
        assert "location" not in searcher.run.call_args.kwargs
    finally:
        release.set()
        service.close()

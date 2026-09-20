import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from shopper.grocery_list import search_grocery_list
from shopper.search import (GrocerySearch, MAX_CONCURRENT_FETCHES, SearchCancelled,
                            SearchError, Source)
from test_search import SETTINGS


def test_list_fetches_share_one_limit_and_release_slots_after_errors():
    searcher = GrocerySearch(SETTINGS)
    searcher.web = MagicMock()
    searcher.web.search.side_effect = lambda query, domains: [
        Source(str(i), "Fruit", f"https://{domains[0]}/{i}") for i in range(2)]
    searcher.standardizer = MagicMock()
    searcher.standardizer.standardize_and_rank.return_value = []
    lock, release, full = threading.Lock(), threading.Event(), threading.Event()
    active = peak = calls = 0

    def fetch(source):
        nonlocal active, peak, calls
        with lock:
            active += 1
            calls += 1
            peak = max(peak, active)
            if active == MAX_CONCURRENT_FETCHES:
                full.set()
        try:
            assert release.wait(3)
            if source.id == "1":
                raise SearchError("Unreadable")
            return source
        finally:
            with lock:
                active -= 1

    searcher.web.fetch.side_effect = fetch
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(search_grocery_list, searcher, ("apples", "bananas", "oranges"))
        try:
            assert full.wait(3)
        finally:
            release.set()
        report = future.result(timeout=3)
    assert peak == MAX_CONCURRENT_FETCHES
    assert calls == 30 and active == 0
    assert report.failed == 0


def test_cancelled_fetches_do_not_call_provider_after_waiting_for_slot(monkeypatch):
    import shopper.search as module

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(module, "_FETCH_SLOTS", slots)
    searcher = GrocerySearch(SETTINGS)
    searcher.web = MagicMock()
    cancelled, entered = threading.Event(), threading.Event()
    source = Source("1", "Apples", "https://www.walmart.ca/apples")
    slots.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(searcher._read_and_rank, "apples", [source], cancelled=cancelled,
                             progress=lambda message: entered.set())
        try:
            assert entered.wait(3)
            cancelled.set()
        finally:
            slots.release()
        with pytest.raises(SearchCancelled):
            future.result(timeout=3)
    searcher.web.fetch.assert_not_called()

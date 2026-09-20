from unittest.mock import MagicMock

import pytest

from shopper.search import BrowserbaseWeb, SearchError, Source


SOURCE = Source("1", "Milk from old search title", "https://www.walmart.ca/old-milk")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_supported_redirect_reads_final_page_and_uses_final_evidence(monkeypatch, status):
    post = MagicMock(side_effect=[
        {"statusCode": status, "headers": {"Location": "/new-milk"}},
        {"statusCode": 200, "content": "Milk 2 L. CAD$4.57. In stock."},
    ])
    monkeypatch.setattr("shopper.search.post_json", post)
    result = BrowserbaseWeb("key").fetch(SOURCE)
    assert result.url == "https://www.walmart.ca/new-milk"
    assert result.content == "Milk 2 L. CAD$4.57. In stock."
    assert result.id == SOURCE.id and result.title == ""
    assert [call.kwargs["payload"]["url"] for call in post.call_args_list] == [SOURCE.url, result.url]
    assert all(call.kwargs["payload"]["allowRedirects"] is False for call in post.call_args_list)


@pytest.mark.parametrize("location", [
    "https://walmart.ca.evil.example/milk", "http://127.0.0.1/milk",
    "file:///etc/passwd", "https://user:pass@www.walmart.ca/milk", "http://[",
    "//unsupported.example/milk", SOURCE.url, "", None, ["/milk"],
])
def test_redirect_is_validated_before_making_next_request(monkeypatch, location):
    post = MagicMock(return_value={"statusCode": 301, "headers": {"location": location}})
    monkeypatch.setattr("shopper.search.post_json", post)
    with pytest.raises(SearchError):
        BrowserbaseWeb("key").fetch(SOURCE)
    post.assert_called_once()


def test_redirect_chain_is_bounded(monkeypatch):
    post = MagicMock(side_effect=[
        {"statusCode": 301, "headers": {"location": f"/redirect-{i}"}} for i in range(3)
    ])
    monkeypatch.setattr("shopper.search.post_json", post)
    with pytest.raises(SearchError):
        BrowserbaseWeb("key").fetch(SOURCE)
    assert post.call_count == 3


def test_supported_redirect_still_requires_readable_final_page(monkeypatch):
    post = MagicMock(side_effect=[
        {"statusCode": 301, "headers": {"location": "/milk"}},
        {"statusCode": 403, "content": "Blocked"},
    ])
    monkeypatch.setattr("shopper.search.post_json", post)
    with pytest.raises(SearchError):
        BrowserbaseWeb("key").fetch(SOURCE)
    assert post.call_count == 2

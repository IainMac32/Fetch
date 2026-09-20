import sys
import types
from datetime import datetime, timezone

from shopper.fetch_orders import FetchOrdersError, load_order_calendar_entries


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, count):
        return self.documents[:count]


class FakeCollection:
    def __init__(self, documents):
        self.documents = documents
        self.last_query = None

    def find(self, query, projection):
        self.last_query = (query, projection)
        return FakeCursor(self.documents)


class FakeDatabase:
    def __init__(self, collection):
        self.collection = collection

    def __getitem__(self, name):
        assert name == "orders"
        return self.collection


class FakeClient:
    def __init__(self, _uri, collection, **_kwargs):
        self.collection = collection
        self.closed = False

    def __getitem__(self, name):
        assert name == "myAppDB"
        return FakeDatabase(self.collection)

    def close(self):
        self.closed = True


def test_load_order_calendar_entries_groups_by_date(monkeypatch):
    collection = FakeCollection([
        {
            "order": "Large pepperoni pizza from Domino's",
            "createdAt": datetime(2026, 9, 28, 18, 30, tzinfo=timezone.utc),
        },
        {
            "order": "Caesar salad",
            "createdAt": datetime(2026, 9, 28, 20, 15, tzinfo=timezone.utc),
        },
        {
            "order": "Turkey sandwich",
            "createdAt": datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc),
        },
    ])
    fake_module = types.SimpleNamespace(MongoClient=lambda uri, **kwargs: FakeClient(uri, collection, **kwargs))
    monkeypatch.setitem(sys.modules, "pymongo", fake_module)

    result = load_order_calendar_entries(
        mongo_uri="mongodb://example",
        phone_number="+15550000001",
    )

    assert collection.last_query == (
        {"PhoneNumber": "+15550000001"},
        {"_id": 0, "order": 1, "createdAt": 1},
    )
    assert result == {
        "2026-09-28": "Order: Large pepperoni pizza from Domino's\nOrder: Caesar salad",
        "2026-09-29": "Order: Turkey sandwich",
    }


def test_load_order_calendar_entries_reports_tls_handshake_failure(monkeypatch):
    def raise_error(_uri, **_kwargs):
        raise RuntimeError("SSL handshake failed: TLSV1_ALERT_INTERNAL_ERROR")

    fake_module = types.SimpleNamespace(MongoClient=raise_error)
    monkeypatch.setitem(sys.modules, "pymongo", fake_module)

    try:
        load_order_calendar_entries(
            mongo_uri="mongodb://example",
            phone_number="+15550000001",
        )
    except FetchOrdersError as exc:
        assert str(exc) == "The order database connection failed during the TLS handshake."
    else:
        raise AssertionError("Expected FetchOrdersError")
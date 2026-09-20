import json
from unittest.mock import MagicMock

import pytest

from shopper.search import ProductStandardizer, SearchError, SearchReport, Source
from test_search import ai_response, offer_data


@pytest.mark.parametrize("name,match_type", [
    ("Nutty Banana Parfait Sliced Bananas, Peanut Butter Greek Yogurt, Granola, Dark Chocolate Chips",
     "ingredient_or_flavour"),
    ("Banana Bread", "ingredient_or_flavour"),
    ("Banana Smoothie", "ingredient_or_flavour"),
    ("Banana Holder", "accessory"),
    ("Banana Special", "uncertain"),
])
def test_banana_keyword_in_other_products_does_not_become_a_recommendation(monkeypatch, name, match_type):
    extraction = offer_data(name_quote=name, attribute_quotes=[], size_quote=None,
                            price_quote=None, availability_quote=None, match_type=match_type)
    monkeypatch.setattr("shopper.search.post_json", MagicMock(return_value=ai_response([extraction])))
    source = Source("1", name, "https://www.ubereats.com/ca/store/example", name)
    choices = ProductStandardizer("test", "test").standardize_and_rank("Banana", [source])
    assert choices == []
    assert SearchReport("Banana", 1, 1, choices).as_text(detailed=False).startswith("No recommended site yet")


@pytest.mark.parametrize("query,name", [("Banana", "Fresh Bananas"), ("Banana bread", "Banana Bread")])
def test_requested_product_itself_is_still_eligible(monkeypatch, query, name):
    extraction = offer_data(name_quote=name, attribute_quotes=[], size_quote=None,
                            price_quote=None, availability_quote=None, match_type="direct_product")
    post = MagicMock(return_value=ai_response([extraction]))
    monkeypatch.setattr("shopper.search.post_json", post)
    source = Source("1", name, "https://www.walmart.ca/product", name)
    choices = ProductStandardizer("test", "test").standardize_and_rank(query, [source])
    assert len(choices) == 1 and choices[0].name_quote == name
    body = post.call_args.kwargs["payload"]
    assert json.loads(body["input"][1]["content"])["query"] == query
    schema = body["text"]["format"]["schema"]["properties"]["offers"]["items"]
    assert "match_type" in schema["required"]


def test_invalid_product_relationship_is_not_accepted(monkeypatch):
    name = "Fresh Bananas"
    extraction = offer_data(name_quote=name, match_type="looks_good")
    monkeypatch.setattr("shopper.search.post_json", MagicMock(return_value=ai_response([extraction])))
    with pytest.raises(SearchError):
        ProductStandardizer("test", "test").standardize_and_rank("Banana", [
            Source("1", name, "https://www.walmart.ca/product", name)])

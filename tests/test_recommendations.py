from dataclasses import replace
from decimal import Decimal

from shopper.grocery_list import GroceryItemResult, GroceryListReport
from shopper.search import Measure, ProductOffer, ProductStandardizer, SearchReport, Source


def offer(site="instacart.ca", name="Milk 2 L", price="3.80", currency=None):
    return ProductOffer(
        source=Source(name, name, f"https://www.{site}/products/{name.split()[0]}"),
        name_quote=name, attribute_quotes=(), merchant_quote=None,
        size_quote="2 L", size=Measure("volume", Decimal(2000)),
        price_quote=f"${price}", price_amount=Decimal(price), currency=currency,
        availability="unknown", availability_quote=None,
    )


def item(query, offers):
    return GroceryItemResult(query, SearchReport(query, len(offers), len(offers), offers))


def test_milk_output_has_actionable_site_price_and_link_without_claiming_currency():
    choices = ProductStandardizer._rank("2 L milk", [
        offer(name="a2 Milk 2 L"), offer(name="Kirkland Milk 2 L", price="6.39"),
    ])
    report = SearchReport("2 L milk", 25, 9, choices, location="Toronto")
    text = report.as_text(detailed=False)
    assert text.startswith("Recommended site: Instacart\na2 Milk 2 L")
    assert "$3.80 (currency unconfirmed)" in text
    assert choices[0].source.url in text
    assert "Toronto" in text and "CAD" not in text and "cheapest" not in text
    assert "Product evidence:" not in text
    assert "Product evidence:" in report.as_text()


def test_list_fallback_uses_coverage_without_combining_unknown_seller_prices():
    report = GroceryListReport((
        item("2 L milk", [offer("walmart.ca"), offer()]),
        item("2 L juice", [offer(name="Juice 2 L")]),
    ))
    text = report.as_text(detailed=False)
    assert text.startswith("Recommended site to start: Instacart")
    assert "2 of 2 items" in text
    assert "cheapest complete basket is not verified" in text
    assert "different sellers" in text
    assert "merchandise total:" not in text


def test_complete_basket_wins_over_coverage_fallback_with_whole_package_total():
    report = GroceryListReport((
        item("3 L milk", [offer("walmart.ca", currency="CAD"), offer()]),
        item("2 L juice", [offer("walmart.ca", name="Juice 2 L", price="5", currency="CAD"),
                          offer(name="Juice 2 L")]),
    ))
    text = report.as_text(detailed=False)
    assert text.startswith("Recommended site: Walmart Canada")
    assert "$12.60 CAD" in text
    assert "3 L milk: 2 package(s)" in text


def test_tied_sites_always_return_one_deterministic_recommendation():
    a, b = offer("walmart.ca"), offer()
    report = GroceryListReport((item("2 L milk", [a, b]), item("2 L milk again", [b, a])))
    assert report.as_text(detailed=False).startswith("Recommended site to start: Instacart")
    swapped = replace(report, items=tuple(reversed(report.items)))
    assert swapped.as_text(detailed=False).startswith("Recommended site to start: Instacart")


def test_partial_list_recommends_available_site_and_names_missing_items():
    report = GroceryListReport((item("milk", [offer()]), GroceryItemResult("apples", error="Unavailable")))
    text = report.as_text(detailed=False)
    assert text.startswith("Recommended site to start: Instacart")
    assert "1 of 2 items" in text
    assert "apples: Search failed: Unavailable" in text


def test_no_matches_does_not_invent_a_recommendation():
    report = GroceryListReport((item("milk", []),))
    assert report.as_text(detailed=False).startswith("No recommended site yet:")
    assert SearchReport("milk", 0, 0, []).as_text(detailed=False).startswith("No recommended site yet:")

"""Bounded grocery lists built from independent, verified product searches."""

import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from urllib.parse import parse_qs, urlsplit

from .search import (SUPPORTED_PLATFORMS, SearchError, SearchReport, check_cancelled,
                     Measure, format_measure, format_money, marketplace_for_url, normalize_location, parse_measure)

MAX_LIST_ITEMS = 10
MAX_LIST_CHARS = 2000


def seller_for_offer(offer):
    """Identify a checkout seller only when the source provides evidence for it."""
    url = urlsplit(offer.source.url)
    marketplace = marketplace_for_url(offer.source.url)
    if not marketplace:
        return None
    host = (url.hostname or "").lower().rstrip(".")
    root = next(domain for domains in SUPPORTED_PLATFORMS.values() for domain in domains
                if host == domain or host.endswith("." + domain))
    if marketplace == "Walmart":
        return root, "Walmart Canada" if root == "walmart.ca" else "Walmart US"
    if marketplace == "Instacart":
        slugs = parse_qs(url.query).get("retailerSlug", [])
        if slugs and re.fullmatch(r"[a-zA-Z0-9-]{2,80}", slugs[0]):
            name = re.sub(r"-+", " ", slugs[0]).strip().title()
            region = "Canada" if root == "instacart.ca" else "US"
            return root + ":" + slugs[0].casefold(), f"Instacart {region} / {name}"
    if offer.merchant_quote:
        name = " ".join(offer.merchant_quote.split())
        key = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
        marketplace_key = re.sub(r"[^a-z0-9]+", " ", marketplace.casefold()).strip()
        if key and key != marketplace_key:
            return root + ":" + key, marketplace + " / " + name
    return None


def requested_measure(query):
    measure = parse_measure(query)
    if measure:
        return measure
    match = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s+\S+", query)
    if not match:
        return None
    try:
        quantity = Decimal(match.group(1).replace(",", "."))
    except (ValueError, ArithmeticError):
        return None
    return Measure("count", quantity) if quantity > 0 else None


def item_packages_needed(query, offer):
    wanted = requested_measure(query)
    size = offer.size
    if (wanted and wanted.kind == "count"
            and re.search(r"\b(?:sold individually|each|1 item)\b", offer.name_quote, re.I)):
        size = Measure("count", Decimal(1))
    if wanted is None or size is None or wanted.kind != size.kind or size.amount <= 0:
        return None
    return int((wanted.amount / size.amount).to_integral_value(rounding=ROUND_CEILING))


def comparable_baskets(items):
    """Return full seller baskets with verified prices and compatible quantities."""
    sellers = {}
    for index, item in enumerate(items):
        if not item.report:
            continue
        for rank, offer in enumerate(item.report.basket_choices, 1):
            identity = seller_for_offer(offer)
            if identity:
                key, name = identity
                sellers.setdefault(key, {"name": name, "items": {}})["items"].setdefault(index, []).append(
                    (rank, offer))

    baskets = []
    for seller in sellers.values():
        lines, currencies, total, valid = [], set(), Decimal(0), True
        for index, item in enumerate(items):
            options = seller["items"].get(index, [])
            priced = []
            for rank, offer in options:
                packages = item_packages_needed(item.query, offer)
                if (packages is None or offer.price_amount is None or not offer.currency
                        or offer.availability == "out_of_stock"):
                    continue
                priced.append((packages * offer.price_amount, rank, packages, offer))
            if not priced:
                valid = False
                break
            item_total, rank, packages, offer = min(priced, key=lambda option: (option[0], option[1]))
            total += item_total
            currencies.add(offer.currency)
            lines.append((item.query, packages, offer))
        if valid and len(currencies) == 1:
            baskets.append({"seller": seller["name"], "total": total,
                            "currency": next(iter(currencies)), "lines": lines})
    return sorted(baskets, key=lambda basket: (basket["currency"], basket["total"],
                                                basket["seller"].casefold()))


def parse_grocery_list(text):
    """Accept commas, semicolons or lines; preserve quantities and compound names."""
    if not isinstance(text, str) or len(text) > MAX_LIST_CHARS:
        raise SearchError(f"Keep your grocery list under {MAX_LIST_CHARS + 1} characters.")
    items, seen = [], set()
    # A comma between digits may be a decimal separator, e.g. 1,5 L milk.
    for part in re.split(r"[\n\r;]|(?<!\d),|,(?!\d)", text):
        part = re.sub(r"^(?:[-*•]\s*|\d+[.)]\s+)", "", part.strip())
        item = " ".join(part.split())
        if not item:
            continue
        if len(item) > 200:
            raise SearchError("Keep each grocery item under 201 characters.")
        if item.casefold() not in seen:
            items.append(item)
            seen.add(item.casefold())
    if not items:
        raise SearchError("Add an item after SEARCH, e.g. SEARCH apples, bananas, oranges.")
    if len(items) > MAX_LIST_ITEMS:
        raise SearchError(f"Search up to {MAX_LIST_ITEMS} items at a time. Please split your list.")
    return tuple(items)


@dataclass(frozen=True)
class GroceryItemResult:
    query: str
    report: SearchReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class GroceryListReport:
    items: tuple[GroceryItemResult, ...]
    location: str | None = None

    @property
    def matched(self):
        return sum(bool(item.report and item.report.choices) for item in self.items)

    @property
    def failed(self):
        return sum(item.error is not None for item in self.items)

    def recommendation_lines(self):
        baskets = comparable_baskets(self.items)
        if baskets and len({basket["currency"] for basket in baskets}) == 1:
            winner = baskets[0]
            return [f"Recommended site: {winner['seller']}",
                    f"Lowest comparable full-basket merchandise total: "
                    f"{format_money(winner['total'])} {winner['currency']}.",
                    *(f"{query}: {packages} package(s) — {offer.source.url}"
                      for query, packages, offer in winner["lines"]),
                    "Total excludes delivery fees, taxes and service charges."]

        # Recommend where to start even if currencies, quantities or sellers are missing.
        # Coverage is per marketplace; this is never presented as a verified full cart.
        sites = {}
        for index, item in enumerate(self.items):
            if not item.report:
                continue
            for rank, offer in enumerate(item.report.basket_choices):
                site = marketplace_for_url(offer.source.url)
                if site and offer.availability != "out_of_stock":
                    sites.setdefault(site, {}).setdefault(index, (rank, offer))
        if not sites:
            return ["No recommended site yet: no verified matches were found.",
                    *(f"{i + 1}. {item.query}: " +
                      (f"Search failed: {item.error}" if item.error else "no verified match")
                      for i, item in enumerate(self.items)),
                    "Try SEARCH again or use more specific item names."]
        site, matches = min(sites.items(), key=lambda entry: (
            -len(entry[1]),
            -sum(offer.price_amount is not None for _, offer in entry[1].values()),
            sum(rank for rank, _ in entry[1].values()), entry[0].casefold()))
        lines = [f"Recommended site to start: {site}",
                 f"Product matches for {len(matches)} of {len(self.items)} items on this site."]
        for index, item in enumerate(self.items):
            if index in matches:
                offer = matches[index][1]
                price = (f"{format_money(offer.price_amount)} {offer.currency or '(currency unconfirmed)'}"
                         if offer.price_amount is not None else "price unconfirmed")
                lines.extend([f"{index + 1}. {item.query}: {offer.name_quote} — {price} per listed package",
                              offer.source.url])
            else:
                lines.append(f"{index + 1}. {item.query}: " +
                             (f"Search failed: {item.error}" if item.error else "no match on this site"))
        lines.append("Suggested by item coverage and product ranking; a cheapest complete basket "
                     "is not verified. Marketplace products may be from different sellers.")
        return lines

    def as_text(self, *, detailed=True):
        recommendation = self.recommendation_lines()
        if not detailed:
            if self.location:
                recommendation.append(f"Search location: {self.location}")
            recommendation.append("Confirm local prices, quantities, stock and delivery fees on the site.")
            return "\n".join(recommendation)
        lines = [f"Grocery list: matches for {self.matched} of {len(self.items)} items."]
        lines.extend(recommendation)
        if self.location:
            lines.extend([f"Search location: {self.location}",
                          "Delivery availability and local prices unverified."])
        for index, item in enumerate(self.items, 1):
            lines.extend(["", f"{index}. {item.query}"])
            if item.error:
                lines.append("Search failed: " + item.error)
            elif not item.report.choices:
                lines.append("No verified matches found.")
            else:
                for rank, offer in enumerate(item.report.choices, 1):
                    seller = seller_for_offer(offer)
                    price = "not confirmed"
                    if offer.price_amount is not None:
                        price = f"{format_money(offer.price_amount)} {offer.currency or '(currency unclear)'}"
                        if offer.unit_price is not None:
                            price += f" ({format_money(offer.unit_price)}{offer.unit_price_label})"
                    stock = {"in_stock": "listed as available", "out_of_stock": "listed as unavailable",
                             "unknown": "not confirmed"}[offer.availability]
                    lines.extend([
                        f"  {rank}) {offer.name_quote}",
                        "  Store: " + (seller[1] if seller else "not confirmed"),
                        "  Package: " + (format_measure(offer.size) if offer.size else "size not confirmed"),
                        f"  Listed price: {price}; availability: {stock}",
                        offer.source.url,
                    ])
        baskets = comparable_baskets(self.items)
        lines.extend(["", "Best-value basket:"])
        if baskets:
            currencies = {basket["currency"] for basket in baskets}
            if len(currencies) > 1:
                lines.append("Verdict: Full baskets were found, but their currencies differ, so there "
                             "is no fair lowest-price comparison.")
                for currency in sorted(currencies):
                    basket = min((candidate for candidate in baskets if candidate["currency"] == currency),
                                 key=lambda candidate: (candidate["total"], candidate["seller"].casefold()))
                    lines.append(f"  Lowest {currency} basket: {basket['seller']} — "
                                 f"{format_money(basket['total'])} {currency}")
            else:
                currency_baskets = [basket for basket in baskets if basket["currency"] == next(iter(currencies))]
                winner = min(currency_baskets,
                             key=lambda basket: (basket["total"], basket["seller"].casefold()))
                lines.append(f"Verdict: {winner['seller']} has the lowest comparable full-basket "
                             f"merchandise total: {format_money(winner['total'])} {winner['currency']}.")
                for query, packages, offer in winner["lines"]:
                    lines.append(f"  {query}: {packages} package(s) at {format_money(offer.price_amount)} "
                                 f"{offer.currency} each — {offer.source.url}")
                alternatives = [basket for basket in currency_baskets if basket is not winner]
                if alternatives:
                    lines.append("Other comparable baskets: " + "; ".join(
                        f"{basket['seller']} {format_money(basket['total'])} {basket['currency']}"
                        for basket in alternatives))
                lines.append("The total includes whole packages needed for the requested amounts.")
        else:
            lines.append("Verdict: No retailer has a complete basket with verified seller, quantity, "
                         "package size and same-currency prices for every item yet.")
            if any(not requested_measure(item.query) for item in self.items):
                lines.append("Add a quantity to every item to enable basket totals.")
        lines.extend(["Basket totals exclude delivery fees, taxes, discounts and service charges. "
                      "Availability may still need confirmation on the retailer's site."])
        return "\n".join(lines)


def search_grocery_list(searcher, items, *, location=None, cancelled=None, progress=lambda message: None):
    """Search items concurrently while keeping a small cap on provider load."""
    # Validate every item before any provider call, including programmatic callers.
    if not isinstance(items, (tuple, list)) or not 1 <= len(items) <= MAX_LIST_ITEMS:
        raise SearchError(f"Search between 1 and {MAX_LIST_ITEMS} grocery items at a time.")
    if any(not isinstance(item, str) or parse_grocery_list(item) != (item,) for item in items):
        raise SearchError("Provide one grocery item per entry, up to 200 characters each.")
    location = normalize_location(location)
    location_options = {"location": location} if location else {}
    cancelled = cancelled if cancelled is not None else threading.Event()
    check_cancelled(cancelled)
    searcher.check_config()
    progress(f"Searching {len(items)} items in parallel (up to 3 at a time)…")
    results = [None] * len(items)

    def search_one(index, item):
        check_cancelled(cancelled)
        prefix = f"Item {index + 1}/{len(items)} ({item}): "
        try:
            run_search = getattr(searcher, "run_all_stores", None) or searcher.run
            report = run_search(item, cancelled=cancelled, **location_options,
                                progress=lambda message, prefix=prefix: progress(prefix + message))
            return GroceryItemResult(item, report=report)
        except SearchError as exc:
            return GroceryItemResult(item, error=str(exc))

    with ThreadPoolExecutor(max_workers=min(3, len(items)), thread_name_prefix="grocery-item") as pool:
        futures = {pool.submit(search_one, index, item): index for index, item in enumerate(items)}
        for future in as_completed(futures):
            check_cancelled(cancelled)
            index = futures[future]
            results[index] = future.result()
            progress(f"Finished item {index + 1}/{len(items)}.")
    return GroceryListReport(tuple(results), location=location)

"""Run the real search pipeline locally, without LINQ."""

import argparse
import logging
import sys

from shopper.config import Settings
from shopper.search import DEMO_SEARCH_QUERY, GrocerySearch, SearchError, normalize_location
from shopper.grocery_list import parse_grocery_list, search_grocery_list


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default=DEMO_SEARCH_QUERY,
                        help="One item or a quoted list separated by commas, semicolons or new lines (max 10).")
    parser.add_argument("--debug", action="store_true",
                        help="Show stage counts and validation reasons without logging keys or page text.")
    parser.add_argument("--location", help='Search near a city and postal code, e.g. "Toronto, ON M5V 2T6".')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    logger = logging.getLogger("shopper.search")
    previous_level = logger.level
    logger.setLevel(logging.INFO if args.debug else logging.ERROR)
    try:
        items = parse_grocery_list(args.query)
        location = normalize_location(args.location)
        location_options = {"location": location} if location else {}
        search = GrocerySearch(Settings.from_env(messaging=False))
        progress = lambda message: print(message, file=sys.stderr)
        report = (search.run(items[0], progress=progress, **location_options) if len(items) == 1
                  else search_grocery_list(search, items, progress=progress, **location_options))
    except (SearchError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        logger.setLevel(previous_level)
    print(report.as_text(detailed=args.debug))
    return 1 if getattr(report, "failed", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())

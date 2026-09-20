"""Run the real search pipeline locally, without LINQ."""

import argparse
import logging
import sys

from shopper.config import Settings
from shopper.search import DEMO_SEARCH_QUERY, GrocerySearch, SearchError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default=DEMO_SEARCH_QUERY)
    parser.add_argument("--debug", action="store_true",
                        help="Show stage counts and validation reasons without logging keys or page text.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    logging.getLogger("shopper.search").setLevel(logging.INFO if args.debug else logging.WARNING)
    try:
        search = GrocerySearch(Settings.from_env(messaging=False))
        report = search.run(args.query, progress=lambda message: print(message, file=sys.stderr))
    except (SearchError, ValueError) as exc:
        print("TWO!")
        print(str(exc), file=sys.stderr)
        print("end two")
        return 1
    print("ONE!")
    print(report.as_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run the real search pipeline locally, without LINQ, MongoDB or a login."""

import argparse
import sys

from shopper.config import Settings
from shopper.search import DEMO_SEARCH_QUERY, GrocerySearch, SearchError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default=DEMO_SEARCH_QUERY)
    args = parser.parse_args()
    try:
        search = GrocerySearch(Settings.from_env(messaging=False))
        report = search.run(args.query, progress=lambda message: print(message, file=sys.stderr))
    except (SearchError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(report.as_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

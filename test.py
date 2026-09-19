"""Run the LINQ grocery search webhook."""
import logging
from shopper.app import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # No reloader: one process owns active searches and recent webhook IDs.
    create_app().run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)

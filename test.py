"""Run the LINQ webhook and DoorDash connection page."""
import logging
from shopper.app import create_app

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # No reloader: one process owns the active Browserbase sessions.
    create_app().run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)

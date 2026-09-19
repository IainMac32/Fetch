"""Run the automated DoorDash login flow against the saved demo credentials."""
import argparse
import getpass
import threading
import time

from shopper.browser import DoorDashBrowser, stagehand_search
from shopper.config import Settings
from shopper.service import DEMO_USER_ID, LoginJob
from shopper.store import Store


def main():
    parser = argparse.ArgumentParser(description="Check automated DoorDash login in Browserbase")
    parser.add_argument("--query", help="After login, run a Stagehand search in the same browser")
    args = parser.parse_args()
    settings = Settings.from_env(messaging=False)
    if args.query and not (settings.model_api_key and settings.stagehand_model):
        parser.error("--query requires MODEL_API_KEY and STAGEHAND_MODEL in .env")
    store = Store(settings.mongo_uri, settings.credential_encryption_key)
    if not store.credentials_for(DEMO_USER_ID):
        parser.error("No saved demo credentials. Start the app and text CONNECT first.")
    job = LoginJob(DEMO_USER_ID, "", settings.public_base_url)

    def monitor():
        prompted = None
        while not job.finished_at:
            state = job.state
            if state in {"awaiting_credentials", "awaiting_code"} and state != prompted:
                prompted = state
                if state == "awaiting_code":
                    code = input("Enter the DoorDash verification code: ").strip()
                    job.submit_code(code)
                else:
                    login = input("DoorDash email or phone: ").strip()
                    password = getpass.getpass("DoorDash password: ")
                    job.submit_sign_in(login, password,
                                       lambda: store.save_credentials(DEMO_USER_ID, login, password))
                continue
            time.sleep(0.25)

    def notify(_message):
        # The CLI is a diagnostic only; the app link belongs to the Flask service's token map.
        print("DoorDash needs attention. Follow the terminal prompt if one appears.", flush=True)

    def resume(session_id, page):
        if args.query:
            stagehand_search(settings, session_id, page, args.query)
            print("Search submitted. No cart or checkout actions performed.", flush=True)

    try:
        print("Starting automated Browserbase sign-in…", flush=True)
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()
        DoorDashBrowser(settings, store).run(
            job, notify, after_login=resume,
            on_status=lambda message: print("Status: " + message, flush=True),
        )
        print("DoorDash connection verified and session released for persistence.", flush=True)
    except KeyboardInterrupt:
        job.cancelled.set()
        print("Cancelled.", flush=True)
    except Exception as exc:
        print(f"Connection did not complete ({type(exc).__name__}).", flush=True)
    finally:
        job.transition("connected", "Finished")


if __name__ == "__main__":
    main()

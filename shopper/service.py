import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .search import DEMO_SEARCH_QUERY, GrocerySearch, SearchCancelled, SearchError, normalize_location
from .grocery_list import MAX_LIST_ITEMS, parse_grocery_list, search_grocery_list

LOG = logging.getLogger(__name__)
HELP = (f"Text SEARCH apples, bananas, oranges (up to {MAX_LIST_ITEMS} items). "
        "Separate items with commas, semicolons or new lines. "
        "Set your area with LOCATION Toronto, ON M5V 2T6; LOCATION shows it and LOCATION CLEAR removes it. "
        "Text SEARCH alone for the demo, STATUS for progress, or CANCEL to stop.")


@dataclass
class SearchJob:
    chat_id: str
    items: tuple[str, ...] = (DEMO_SEARCH_QUERY,)
    location: str | None = None
    message: str = "Starting grocery search…"
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def set_message(self, message):
        with self.lock:
            if not self.cancelled.is_set():
                self.message = message

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            self.message = "Search cancelled. Text SEARCH to start again."

    def notify(self, messenger, text):
        with self.lock:
            if not self.cancelled.is_set():
                messenger.send(self.chat_id, text)


class SearchService:
    """Bounded search and message workers for a single-process prototype."""

    def __init__(self, settings, messenger, *, searcher=None):
        self.settings, self.messenger = settings, messenger
        self.lock = threading.RLock()
        self.closed = False
        self.searcher = searcher if searcher is not None else GrocerySearch(settings)
        self.search_job = None
        self.locations = {}  # Per chat, for this process only.
        self.search_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="grocery-search")
        # Preserve command order so CANCEL cannot overtake an accepted SEARCH.
        self.message_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="linq")
        self.message_slots = threading.BoundedSemaphore(32)

    def submit(self, incoming):
        with self.lock:
            if self.closed or not self.message_slots.acquire(blocking=False):
                return False
            try:
                self.message_pool.submit(self._handle, incoming)
                return True
            except RuntimeError:
                self.message_slots.release()
                return False

    def _handle(self, incoming):
        try:
            if incoming.sender_handle != self.settings.demo_user_handle:
                return
            command = incoming.text.strip().lower()
            with self.lock:
                search_job = self.search_job
            if search_job and not search_job.finished.is_set() and search_job.chat_id != incoming.chat_id:
                self.messenger.send(incoming.chat_id, "This prototype is connected to one demo iMessage chat.")
                return
            if command in {"stop", "cancel", "unsubscribe", "quit", "end", "opt out", "optout"}:
                if search_job and search_job.chat_id == incoming.chat_id:
                    search_job.cancel()
                return
            if re.match(r"^location(?:\s|:|$)", command):
                value = re.sub(r"^location\s*:?\s*", "", incoming.text.strip(), count=1, flags=re.I)
                self.set_location(incoming.chat_id, value)
            elif re.match(r"^search(?:\s|:|$)", command):
                query = re.sub(r"^search\s*:?\s*", "", incoming.text.strip(), count=1, flags=re.I)
                self.start_search(incoming.chat_id, DEMO_SEARCH_QUERY if command == "search" else query)
            elif command == "status" and search_job and search_job.chat_id == incoming.chat_id:
                self.messenger.send(incoming.chat_id, search_job.message)
            else:
                self.messenger.send(incoming.chat_id, HELP)
        except Exception as exc:
            LOG.error("Message handling failed (%s)", type(exc).__name__)
        finally:
            self.message_slots.release()

    def set_location(self, chat_id, value):
        with self.lock:
            if not value:
                location = self.locations.get(chat_id)
                reply = (f"Search location: {location}" if location else
                         "No location set. Text LOCATION Toronto, ON M5V 2T6.")
            elif value.casefold() == "clear":
                self.locations.pop(chat_id, None)
                reply = "Location cleared for future searches."
            else:
                try:
                    location = normalize_location(value)
                except SearchError as exc:
                    reply = str(exc)
                else:
                    self.locations[chat_id] = location
                    reply = (f"Location set to {location} for future searches. "
                             "Saved until the server restarts. Delivery availability remains unverified.")
        self.messenger.send(chat_id, reply)

    def start_search(self, chat_id, query=DEMO_SEARCH_QUERY):
        with self.lock:
            if self.closed:
                return None
            try:
                items = parse_grocery_list(query)
                self.searcher.check_config()
            except SearchError as exc:
                reply = str(exc)
            else:
                existing = self.search_job
                if existing and not existing.finished.is_set():
                    reply = (existing.message if existing.chat_id == chat_id
                             else "This prototype is connected to one demo iMessage chat.")
                else:
                    job = SearchJob(chat_id, items=items, location=self.locations.get(chat_id))
                    self.search_job = job
                    self.search_pool.submit(self._run_search, job)
                    return job
        self.messenger.send(chat_id, reply)
        return None

    def _run_search(self, job):
        notify = lambda text: job.notify(self.messenger, text)
        location_options = {"location": job.location} if job.location else {}
        try:
            notify('Searching on DoorDash, Uber Eats, SkipTheDishes, Instacart and Walmart:\n'
                   + (f"Search location: {job.location}\n" if job.location else "")
                   + "\n".join(f"{index}. {item}" for index, item in enumerate(job.items, 1)))
            if len(job.items) == 1:
                report = self.searcher.run(job.items[0], cancelled=job.cancelled, progress=job.set_message,
                                           **location_options)
                job.set_message(f"Search complete: {len(report.choices)} matches from {report.fetched} readable pages. Text SEARCH to run again.")
            else:
                report = search_grocery_list(self.searcher, job.items, cancelled=job.cancelled,
                                             progress=job.set_message, **location_options)
                job.set_message(f"Search complete: matches for {report.matched}/{len(job.items)} items; "
                                f"{report.failed} searches failed. Send SEARCH with an item to retry it.")
            notify(report.as_text(detailed=False))
        except SearchCancelled:
            job.cancel()
        except Exception as exc:
            LOG.error("Grocery search failed (%s)", type(exc).__name__)
            message = str(exc) if isinstance(exc, SearchError) else "Search couldn't finish. Text SEARCH to try again."
            job.set_message(message)
            self._notify_safely(notify, message)
        finally:
            job.finished.set()

    @staticmethod
    def _notify_safely(notify, text):
        try:
            notify(text)
        except Exception as exc:
            LOG.error("Notification failed (%s)", type(exc).__name__)

    def close(self):
        with self.lock:
            self.closed = True
            job = self.search_job
        if job:
            job.cancel()
        self.message_pool.shutdown(wait=True, cancel_futures=True)
        self.search_pool.shutdown(wait=True, cancel_futures=True)

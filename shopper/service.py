import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .search import DEMO_SEARCH_QUERY, GrocerySearch, SearchCancelled, SearchError

LOG = logging.getLogger(__name__)
HELP = "Text SEARCH to try the grocery web search demo, STATUS to check progress, or CANCEL to stop."


@dataclass
class SearchJob:
    chat_id: str
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
            if command == "search":
                self.start_search(incoming.chat_id)
            elif command == "status" and search_job and search_job.chat_id == incoming.chat_id:
                self.messenger.send(incoming.chat_id, search_job.message)
            else:
                self.messenger.send(incoming.chat_id, HELP)
        except Exception as exc:
            LOG.error("Message handling failed (%s)", type(exc).__name__)
        finally:
            self.message_slots.release()

    def start_search(self, chat_id):
        with self.lock:
            if self.closed:
                return None
            try:
                self.searcher.check_config()
            except SearchError as exc:
                reply = str(exc)
            else:
                existing = self.search_job
                if existing and not existing.finished.is_set():
                    reply = (existing.message if existing.chat_id == chat_id
                             else "This prototype is connected to one demo iMessage chat.")
                else:
                    job = SearchJob(chat_id)
                    self.search_job = job
                    self.search_pool.submit(self._run_search, job)
                    return job
        self.messenger.send(chat_id, reply)
        return None

    def _run_search(self, job):
        notify = lambda text: job.notify(self.messenger, text)
        try:
            notify(f'Searching for "{DEMO_SEARCH_QUERY}". I’ll read up to 10 web results and compare the best matches.')
            report = self.searcher.run(DEMO_SEARCH_QUERY, cancelled=job.cancelled, progress=job.set_message)
            job.set_message(f"Search complete: {len(report.choices)} matches from {report.fetched} readable pages. Text SEARCH to run again.")
            notify(report.as_text())
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

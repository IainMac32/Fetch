import hashlib
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from queue import Full, Queue

from .browser import LoginCancelled, LoginExpired

LOG = logging.getLogger(__name__)
HELP = "Text CONNECT to connect your DoorDash account, STATUS to check progress, or CANCEL to stop."
DEMO_USER_ID = "demo-user"


@dataclass
class LoginJob:
    user_key: str
    chat_id: str
    base_url: str
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    cancelled: threading.Event = field(default_factory=threading.Event, repr=False)
    commands: Queue = field(default_factory=lambda: Queue(maxsize=64), repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    state: str = "starting"
    message: str = "Opening DoorDash…"
    deadline: float = 0
    finished_at: float = 0

    @property
    def link(self):
        # Fragment tokens stay out of HTTP access logs and referrer headers.
        return f"{self.base_url}/connect#{self.token}"

    def transition(self, state, message):
        with self.lock:
            self.state, self.message = state, message
            while not self.commands.empty():
                self.commands.get_nowait()
            if state in {"connected", "failed", "expired", "cancelled"}:
                self.finished_at = time.monotonic()

    def set_message(self, message):
        with self.lock:
            self.message = message

    def begin_credentials(self, timeout):
        with self.lock:
            self.deadline = time.monotonic() + timeout
            self.state = "awaiting_credentials"
            self.message = "Enter your DoorDash username and password."

    def submit_sign_in(self, login, password, persist):
        with self.lock:
            if self.state != "awaiting_credentials" or not self.snapshot()["can_control"]:
                return False
            if self.commands.full():
                return False
            persist()
            self.commands.put_nowait(("credentials", {"login": login, "password": password}))
            self.state, self.message = "submitting", "Signing in to DoorDash…"
            return True

    def submit_code(self, code):
        with self.lock:
            if self.state != "awaiting_code" or not self.snapshot()["can_control"]:
                return False
            try:
                self.commands.put_nowait(("code", code))
            except Full:
                return False
            self.state, self.message = "verifying_code", "Checking the DoorDash code…"
            return True

    def snapshot(self):
        with self.lock:
            remaining = max(0, int(self.deadline - time.monotonic()))
            active = self.state in {"awaiting_credentials", "awaiting_code"} and remaining > 0 and not self.cancelled.is_set()
            return {"state": self.state, "message": self.message,
                    "seconds_remaining": remaining, "can_control": active}

    def enqueue(self, action, value=None):
        with self.lock:
            if not self.snapshot()["can_control"]:
                return False
            try:
                self.commands.put_nowait((action, value))
                return True
            except Full:
                return False


class ConnectionService:
    """Bounded, single-process prototype workers; never share Playwright objects."""

    def __init__(self, settings, store, messenger, runner):
        self.settings, self.store, self.messenger, self.runner = settings, store, messenger, runner
        self.lock = threading.RLock()
        self.jobs = {}
        self.tokens = {}
        self.closed = False
        self.browser_pool = ThreadPoolExecutor(max_workers=settings.max_sessions, thread_name_prefix="doordash")
        self.message_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="linq")
        self.message_slots = threading.BoundedSemaphore(32)

    def submit(self, incoming):
        if self.closed:
            return False
        if not self.message_slots.acquire(blocking=False):
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
                job = self.jobs.get(DEMO_USER_ID)
            if job and not job.finished_at and job.chat_id != incoming.chat_id:
                self.messenger.send(incoming.chat_id, "This prototype is connected to one demo iMessage chat.")
                return
            if command in {"stop", "cancel", "unsubscribe", "quit", "end", "opt out", "optout"}:
                if job:
                    job.cancelled.set()
                return
            if command in {"connect", "login", "sign in", "connect doordash"}:
                self.start(incoming.user_key, incoming.chat_id)
            elif command == "status":
                if job:
                    self.messenger.send(incoming.chat_id, job.snapshot()["message"])
                else:
                    self.messenger.send(incoming.chat_id, HELP)
            else:
                self.messenger.send(incoming.chat_id, HELP)
        except Exception as exc:
            LOG.error("Message handling failed (%s)", type(exc).__name__)
            self.store.forget_event(incoming.event_id)
        finally:
            self.message_slots.release()

    def start(self, user_key, chat_id):
        user_key = DEMO_USER_ID
        with self.lock:
            if self.closed:
                return None
            self._prune()
            existing = self.jobs.get(user_key)
            if existing and not existing.finished_at:
                if existing.chat_id != chat_id:
                    reply, job = "This prototype is connected to one demo iMessage chat.", None
                else:
                    reply = existing.message
                    if existing.snapshot()["can_control"]:
                        reply += "\n" + existing.link
                    job = None
            elif sum(not item.finished_at for item in self.jobs.values()) >= self.settings.max_sessions:
                reply, job = "All browsers are busy. Please text CONNECT again in a few minutes.", None
            else:
                job = LoginJob(user_key, chat_id, self.settings.public_base_url)
                # This prototype deliberately uses exactly one shared DoorDash account.
                self.jobs[DEMO_USER_ID] = job
                self.tokens[hashlib.sha256(job.token.encode()).hexdigest()] = job
                reply = None
                self.browser_pool.submit(self._run, job)
        if reply:
            self.messenger.send(chat_id, reply)
        return job

    def _run(self, job):
        def notify(text):
            if not job.cancelled.is_set():
                self.messenger.send(job.chat_id, text)
        try:
            self.runner.run(job, notify)
            if job.cancelled.is_set():
                raise LoginCancelled()
            job.transition("connected", "DoorDash is connected. You can close this page.")
            notify("Your DoorDash account is connected. Your sign-in will be reused for future shopping sessions.")
        except LoginExpired:
            job.transition("expired", "This connection timed out. Text CONNECT for a new link.")
            self._notify_safely(notify, job.message)
        except LoginCancelled:
            job.transition("cancelled", "Connection cancelled. Text CONNECT when you're ready.")
        except Exception as exc:
            LOG.error("DoorDash connection failed (%s)", type(exc).__name__)
            job.transition("failed", "Couldn't finish connecting to DoorDash. Text CONNECT to try again.")
            self._notify_safely(notify, job.message)

    @staticmethod
    def _notify_safely(notify, text):
        try:
            notify(text)
        except Exception as exc:
            LOG.error("Notification failed (%s)", type(exc).__name__)

    def _prune(self):
        cutoff = time.monotonic() - 300
        self.tokens = {key: job for key, job in self.tokens.items() if not job.finished_at or job.finished_at > cutoff}
        self.jobs = {key: job for key, job in self.jobs.items() if not job.finished_at or job.finished_at > cutoff}

    def lookup(self, token):
        if not token or len(token) > 100:
            return None
        with self.lock:
            self._prune()
            return self.tokens.get(hashlib.sha256(token.encode()).hexdigest())

    def close(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                job.cancelled.set()
        self.message_pool.shutdown(wait=True, cancel_futures=True)
        self.browser_pool.shutdown(wait=True, cancel_futures=True)

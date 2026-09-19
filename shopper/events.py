"""Process-local webhook deduplication; entries do not survive a restart."""

import threading
import time
from collections import OrderedDict


class RecentEvents:
    def __init__(self, *, ttl_seconds=86400, capacity=10000, clock=time.monotonic):
        if ttl_seconds <= 0 or capacity <= 0:
            raise ValueError("Event TTL and capacity must be positive.")
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self.clock = clock
        self.events = OrderedDict()
        self.lock = threading.Lock()

    def claim(self, event_id):
        """Return True if claimed, False if duplicate, or None if full."""
        with self.lock:
            now = self.clock()
            while self.events and next(iter(self.events.values())) <= now:
                self.events.popitem(last=False)
            if event_id in self.events:
                return False
            if len(self.events) >= self.capacity:
                return None
            self.events[event_id] = now + self.ttl_seconds
            return True

    def forget(self, event_id):
        with self.lock:
            self.events.pop(event_id, None)

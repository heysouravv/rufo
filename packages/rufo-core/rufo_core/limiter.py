from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from rufo_core.models import Limit


class RateLimiter:
    """In-memory sliding-window rate limiter.

    Not distributed and not persisted across process restarts -- fine for a
    single runtime instance. A Redis-backed limiter is the natural upgrade
    path once deployments span multiple runtime processes.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, scope: str, limit: Limit) -> bool:
        """Record a call against `scope` and return True if it exceeds `limit`."""
        now = self._clock()
        with self._lock:
            window = self._hits[scope]
            window.append(now)
            cutoff = now - limit.per_seconds
            while window and window[0] < cutoff:
                window.popleft()
            return len(window) > limit.max_calls

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

"""Sliding-window rate limiting with a pluggable store.

* MemoryStore: per process (default; correct for the single-instance deployment this project supports today).
* RedisStore: shared by every instance behind a load balancer (COSMO_REDIS_URL). If Redis is unreachable the limiter degrades to the in-process
  store for that request and counts the failure (metric `cosmo_ratelimit_store_errors_total`) instead of rejecting every user or disabling limits.
Both implement `hit(key, limit, window) -> (allowed, retry_after_seconds)` and count every ATTEMPT, so a rejected request does not extend its own window."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque


class MemoryStore:
    name = "memory"

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False, max(1, int(q[0] + window - now))
            q.append(now)
            return True, 0

    def count(self, key: str, window: int) -> int:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            return len(q)

    def clear(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def reset(self):
        with self._lock:
            self._hits.clear()


class RedisStore:
    name = "redis"
    PREFIX = "cosmo:rl:"

    def __init__(self, client):
        self.r = client

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.time()
        k = self.PREFIX + key
        member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
        pipe = self.r.pipeline(transaction=True)              # MULTI/EXEC: the three steps are atomic per key
        pipe.zremrangebyscore(k, 0, now - window)
        pipe.zadd(k, {member: now})
        pipe.zcard(k)
        pipe.zrange(k, 0, 0, withscores=True)
        pipe.expire(k, window + 5)
        _, _, count, oldest, _ = pipe.execute()
        if count > limit:
            self.r.zrem(k, member)                            # the rejected attempt is not counted against the window
            retry = max(1, int(oldest[0][1] + window - now)) if oldest else window
            return False, retry
        return True, 0

    def count(self, key: str, window: int) -> int:
        k = self.PREFIX + key
        self.r.zremrangebyscore(k, 0, time.time() - window)
        return int(self.r.zcard(k))

    def clear(self, key: str) -> None:
        self.r.delete(self.PREFIX + key)

    def reset(self):
        for k in self.r.scan_iter(self.PREFIX + "*"):
            self.r.delete(k)

    def ping(self) -> bool:
        return bool(self.r.ping())


class Limiter:
    """Chooses the store, falls back to memory on Redis errors."""

    def __init__(self, store=None, fallback=None):
        self.fallback = fallback or MemoryStore()
        self.store = store or self.fallback
        self.errors = 0

    @classmethod
    def from_env(cls, env=os.environ):
        url = env.get("COSMO_REDIS_URL")
        if not url:
            return cls()
        import redis
        client = redis.Redis.from_url(url, socket_timeout=0.25, socket_connect_timeout=0.5, decode_responses=False)
        return cls(RedisStore(client))

    def hit(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        try:
            return self.store.hit(key, limit, window)
        except Exception:
            self.errors += 1
            return self.fallback.hit(key, limit, window)

    def count(self, key: str, window: int) -> int:
        try:
            return self.store.count(key, window)
        except Exception:
            self.errors += 1
            return self.fallback.count(key, window)

    def clear(self, key: str) -> None:
        try:
            self.store.clear(key)
        except Exception:
            self.errors += 1
        self.fallback.clear(key)

    def reset(self):
        self.fallback.reset()
        try:
            self.store.reset()
        except Exception:
            pass


_limiter: Limiter | None = None


def get() -> Limiter:
    global _limiter
    if _limiter is None:
        _limiter = Limiter.from_env()
    return _limiter


def set_limiter(lim: Limiter | None):
    global _limiter
    _limiter = lim

"""In-process sliding-window rate limiter.

Deliberately simple: a deque of timestamps per key. This is correct for one
process and documented as wrong for many -- see ARCHITECTURE.md §7. A Redis
backed implementation would keep the same interface.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        bucket = self._hits[key]

        while bucket and now - bucket[0] > self.window:
            bucket.popleft()

        if len(bucket) >= self.limit:
            retry_after = int(self.window - (now - bucket[0])) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit of {self.limit}/min exceeded. Retry in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)

    def reset(self) -> None:
        """Test helper -- drops every bucket."""
        self._hits.clear()


def client_key(request: Request) -> str:
    """Best-effort client identity.

    X-Forwarded-For is trusted here because the app is expected to sit behind a
    proxy. Behind no proxy it is client-controlled, so rate limiting becomes
    advisory -- stated plainly rather than pretended otherwise.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

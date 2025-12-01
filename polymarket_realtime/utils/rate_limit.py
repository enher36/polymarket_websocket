"""Async rate limiting using token bucket algorithm."""

import asyncio
import time


class AsyncRateLimiter:
    """Token bucket rate limiter for async operations.

    Implements a simple token bucket algorithm to limit the rate of
    operations. Tokens are replenished at a constant rate.

    Example:
        limiter = AsyncRateLimiter(rate=2.0)  # 2 requests per second
        async with limiter:
            await make_request()
    """

    def __init__(self, rate: float, capacity: int | None = None) -> None:
        """Initialize rate limiter.

        Args:
            rate: Tokens per second to replenish.
            capacity: Maximum tokens (defaults to 2x rate).
        """
        self.rate = rate
        self.capacity = capacity or max(1, int(rate * 2))
        self._tokens = float(self.capacity)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire a token, waiting if necessary."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._last_update = now

            # Replenish tokens
            self._tokens = min(
                self.capacity,
                self._tokens + elapsed * self.rate
            )

            # Wait if no tokens available
            if self._tokens < 1:
                wait_time = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait_time)
                self._tokens = 0.0
            else:
                self._tokens -= 1

    async def __aenter__(self) -> "AsyncRateLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

"""Retry utilities with exponential backoff."""

from collections.abc import Awaitable, Callable
from typing import ParamSpec, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

P = ParamSpec("P")
T = TypeVar("T")


async def async_retry(
    fn: Callable[P, Awaitable[T]],
    *args: P.args,
    attempts: int = 3,
    base_wait: float = 0.2,
    max_wait: float = 2.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    **kwargs: P.kwargs,
) -> T:
    """Execute an async function with retry logic.

    Uses exponential backoff between retries.

    Args:
        fn: Async function to execute.
        *args: Positional arguments to pass to fn.
        attempts: Maximum number of attempts.
        base_wait: Base wait time in seconds.
        max_wait: Maximum wait time in seconds.
        retry_on: Exception types to retry on.
        **kwargs: Keyword arguments to pass to fn.

    Returns:
        Result from successful function execution.

    Raises:
        The last exception if all retries fail.
    """
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=base_wait, max=max_wait),
            retry=retry_if_exception_type(retry_on),
            reraise=True,
        ):
            with attempt:
                return await fn(*args, **kwargs)
    except RetryError as e:
        raise e.last_attempt.result() from e

    # This should never be reached, but satisfies type checker
    raise RuntimeError("Retry logic failed unexpectedly")

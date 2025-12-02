"""In-process event bus for forwarding real-time messages."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, DefaultDict, Set

from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)

# Type alias for forward callbacks
ForwardCallback = Callable[["ForwardEvent"], Awaitable[None]]


@dataclass(slots=True)
class ForwardEvent:
    """Event emitted for downstream forwarding.

    Attributes:
        token_id: The token identifier this event relates to.
        event_type: Type of event (book, price_change, last_trade_price, etc.).
        payload: Event data as dictionary.
        timestamp: When the event was created.
    """

    token_id: str
    event_type: str
    payload: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    """Lightweight async event bus keyed by token_id.

    Supports:
    - Per-token subscriptions
    - Wildcard '*' subscription for all events
    - Async callbacks
    """

    def __init__(self) -> None:
        """Initialize the event bus."""
        self._subscribers: DefaultDict[str, Set[ForwardCallback]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, event: ForwardEvent) -> None:
        """Publish an event to all subscribers.

        Sends to:
        1. All callbacks registered for the specific token_id
        2. All callbacks registered for wildcard '*'

        Args:
            event: The event to publish.
        """
        async with self._lock:
            # Get callbacks for specific token and wildcard
            callbacks = list(self._subscribers.get(event.token_id, set()))
            callbacks += list(self._subscribers.get("*", set()))

        # Execute callbacks outside lock
        for callback in callbacks:
            try:
                await callback(event)
            except Exception as exc:
                logger.error(
                    "Forward event callback failed",
                    extra={
                        "ctx_error": str(exc),
                        "ctx_token_id": event.token_id,
                        "ctx_event_type": event.event_type,
                    },
                )

    async def subscribe(self, token_id: str, callback: ForwardCallback) -> None:
        """Register a callback for events on a token.

        Args:
            token_id: Token to subscribe to, or '*' for all events.
            callback: Async function to call with ForwardEvent.
        """
        async with self._lock:
            self._subscribers[token_id].add(callback)
        logger.debug(
            "Registered forward subscriber",
            extra={"ctx_token_id": token_id},
        )

    async def unsubscribe(self, token_id: str, callback: ForwardCallback) -> None:
        """Remove a callback from a token subscription.

        Args:
            token_id: Token to unsubscribe from.
            callback: The callback to remove.
        """
        async with self._lock:
            callbacks = self._subscribers.get(token_id)
            if callbacks and callback in callbacks:
                callbacks.discard(callback)
                if not callbacks:
                    self._subscribers.pop(token_id, None)
        logger.debug(
            "Removed forward subscriber",
            extra={"ctx_token_id": token_id},
        )

    async def unsubscribe_all(self, token_id: str | None = None) -> int:
        """Forcefully clear subscriptions to avoid leaks when clients disconnect.

        Args:
            token_id: Specific token to clear, or None to clear all.

        Returns:
            Number of subscriptions cleared.
        """
        async with self._lock:
            if token_id:
                callbacks = self._subscribers.pop(token_id, set())
                cleared = len(callbacks)
            else:
                cleared = sum(len(cbs) for cbs in self._subscribers.values())
                self._subscribers.clear()
        logger.debug(
            "Cleared forward subscribers",
            extra={"ctx_token_id": token_id or "*", "ctx_cleared": cleared},
        )
        return cleared

    @property
    def subscriber_count(self) -> int:
        """Get total number of subscriptions."""
        return sum(len(cbs) for cbs in self._subscribers.values())


# Shared singleton instance
event_bus = EventBus()

__all__ = ["EventBus", "ForwardEvent", "ForwardCallback", "event_bus"]

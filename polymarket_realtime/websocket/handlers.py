"""WebSocket message handlers for Polymarket data streams."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Coroutine, Optional

from polymarket_realtime.database.repository import Database
from polymarket_realtime.forward.event_bus import ForwardEvent, event_bus
from polymarket_realtime.schemas import OrderbookSnapshot, Trade
from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)

# Type alias for message handlers
MessageHandler = Callable[[dict[str, Any], Database], Coroutine[Any, Any, None]]

# Sequence tracking for orderbook integrity
# Maps token_id -> (last_sequence, has_snapshot)
_orderbook_state: dict[str, tuple[int, bool]] = {}


def _parse_timestamp(ts: int | str | None) -> datetime:
    """Parse various timestamp formats to datetime.

    Handles:
    - int: Unix timestamp in milliseconds
    - str (digits only): Unix timestamp as string
    - str (ISO format): ISO 8601 datetime
    - None: Current UTC time
    """
    if ts is None:
        return datetime.now(timezone.utc)

    if isinstance(ts, int):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)

    if isinstance(ts, str):
        # Check if string is all digits (millisecond timestamp as string)
        if ts.isdigit():
            return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
        # Try ISO format
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Could not parse timestamp", extra={"ctx_ts": ts})
            return datetime.now(timezone.utc)

    return datetime.now(timezone.utc)


async def _publish_forward_event(
    token_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    """Publish an event to the forward bus for downstream consumers.

    Args:
        token_id: Token identifier.
        event_type: Type of event.
        payload: Event data.
    """
    try:
        await event_bus.publish(
            ForwardEvent(
                token_id=token_id,
                event_type=event_type,
                payload=payload,
            )
        )
    except Exception as exc:
        logger.error(
            "Failed to publish forward event",
            extra={
                "ctx_token_id": token_id,
                "ctx_event_type": event_type,
                "ctx_error": str(exc),
            },
        )


async def handle_trade_message(data: dict[str, Any], db: Database) -> None:
    """Handle trade messages from WebSocket.

    Expected format:
    {
        "type": "trade",
        "market": "<token_id>",
        "price": "0.51",
        "size": "25",
        "side": "buy",
        "ts": 1716322234000,
        "id": "trade-uuid"
    }
    """
    try:
        trade_id = data.get("id") or data.get("trade_id")
        if not trade_id:
            logger.warning("Trade missing ID", extra={"ctx_data": str(data)[:200]})
            return

        token_id = data.get("market") or data.get("asset_id")
        if not token_id:
            logger.warning("Trade missing token_id", extra={"ctx_data": str(data)[:200]})
            return

        # Parse timestamp
        ts = data.get("ts") or data.get("timestamp") or data.get("created_at")
        timestamp = _parse_timestamp(ts)

        trade = Trade(
            trade_id=str(trade_id),
            token_id=str(token_id),
            price=Decimal(str(data.get("price", "0"))),
            amount=Decimal(str(data.get("size", data.get("amount", "0")))),
            taker_side=data.get("side", data.get("taker_side", "")),
            timestamp=timestamp,
        )

        saved = await db.save_trade(trade)
        if saved:
            logger.debug(
                "Saved trade",
                extra={
                    "ctx_trade_id": trade.trade_id,
                    "ctx_token_id": trade.token_id,
                    "ctx_price": str(trade.price),
                },
            )

        # Publish to forward bus
        event_type = str(data.get("event_type") or data.get("type") or "trade")
        await _publish_forward_event(
            token_id=str(token_id),
            event_type=event_type,
            payload={
                "trade_id": trade.trade_id,
                "token_id": str(token_id),
                "price": str(trade.price),
                "amount": str(trade.amount),
                "taker_side": trade.taker_side,
                "timestamp": trade.timestamp.isoformat(),
            },
        )

    except Exception as e:
        logger.error(
            "Failed to handle trade message",
            extra={"ctx_error": str(e), "ctx_data": str(data)[:500]},
        )


async def handle_orderbook_message(data: dict[str, Any], db: Database) -> None:
    """Handle orderbook (L2) messages from WebSocket.

    Implements sequence tracking to ensure data integrity:
    - Snapshots reset the sequence and mark the token as having a baseline
    - Updates (l2update) are only applied if sequence is valid
    - Out-of-order or stale updates are dropped

    Expected formats:
    Snapshot:
    {
        "type": "snapshot",
        "channel": "l2",
        "market": "<token_id>",
        "bids": [["0.45", "10000"], ...],
        "asks": [["0.55", "8000"], ...],
        "seq": 1
    }

    Update:
    {
        "type": "l2update",
        "channel": "l2",
        "market": "<token_id>",
        "bids": [["0.46", "5000"]],
        "asks": [],
        "seq": 2
    }
    """
    try:
        token_id = data.get("market") or data.get("asset_id")
        if not token_id:
            logger.warning("Orderbook missing token_id", extra={"ctx_data": str(data)[:200]})
            return

        token_id = str(token_id)
        msg_type = data.get("type", "").lower()
        sequence = data.get("seq") or data.get("sequence")

        # Get current state for this token
        last_seq, has_snapshot = _orderbook_state.get(token_id, (-1, False))

        # Determine event type for forwarding
        event_type = str(data.get("event_type") or msg_type or "book")

        # Handle snapshot - always accept and reset state
        if msg_type == "snapshot":
            snapshot = OrderbookSnapshot.from_ws_message(token_id, data)
            await db.upsert_orderbook(snapshot)
            _orderbook_state[token_id] = (sequence or 0, True)
            logger.debug(
                "Processed orderbook snapshot",
                extra={
                    "ctx_token_id": token_id,
                    "ctx_bids": len(snapshot.bids),
                    "ctx_asks": len(snapshot.asks),
                    "ctx_sequence": sequence,
                },
            )
            await _publish_forward_event(
                token_id=token_id,
                event_type=event_type,
                payload=snapshot.model_dump(mode="json"),
            )
            return

        # Handle update - validate sequence
        if msg_type == "l2update":
            # Require snapshot before accepting updates
            if not has_snapshot:
                logger.warning(
                    "Dropping l2update - no snapshot received yet",
                    extra={"ctx_token_id": token_id, "ctx_sequence": sequence},
                )
                return

            # Check sequence order (if sequence is provided)
            if sequence is not None and last_seq >= 0:
                if sequence <= last_seq:
                    logger.debug(
                        "Dropping stale l2update",
                        extra={
                            "ctx_token_id": token_id,
                            "ctx_sequence": sequence,
                            "ctx_last_seq": last_seq,
                        },
                    )
                    return
                # Check for gaps
                if sequence > last_seq + 1:
                    logger.warning(
                        "Sequence gap detected in orderbook",
                        extra={
                            "ctx_token_id": token_id,
                            "ctx_expected": last_seq + 1,
                            "ctx_received": sequence,
                        },
                    )
                    # Continue anyway - partial data better than no data

            snapshot = OrderbookSnapshot.from_ws_message(token_id, data)
            await db.upsert_orderbook(snapshot)
            _orderbook_state[token_id] = (sequence or last_seq, True)
            logger.debug(
                "Applied orderbook update",
                extra={
                    "ctx_token_id": token_id,
                    "ctx_bids": len(snapshot.bids),
                    "ctx_asks": len(snapshot.asks),
                    "ctx_sequence": sequence,
                },
            )
            await _publish_forward_event(
                token_id=token_id,
                event_type=event_type,
                payload=snapshot.model_dump(mode="json"),
            )
            return

        # Fallback for unknown types - just process
        snapshot = OrderbookSnapshot.from_ws_message(token_id, data)
        await db.upsert_orderbook(snapshot)
        logger.debug(
            "Updated orderbook (unknown type)",
            extra={
                "ctx_token_id": token_id,
                "ctx_type": msg_type,
                "ctx_sequence": sequence,
            },
        )
        await _publish_forward_event(
            token_id=token_id,
            event_type=event_type,
            payload=snapshot.model_dump(mode="json"),
        )

    except Exception as e:
        logger.error(
            "Failed to handle orderbook message",
            extra={"ctx_error": str(e), "ctx_data": str(data)[:500]},
        )


def reset_orderbook_state(token_id: str | None = None) -> None:
    """Reset orderbook sequence tracking state.

    Args:
        token_id: Specific token to reset, or None to reset all.
    """
    global _orderbook_state
    if token_id:
        _orderbook_state.pop(token_id, None)
    else:
        _orderbook_state.clear()


async def route_message(raw_message: str, db: Database) -> None:
    """Route incoming WebSocket message to appropriate handler.

    Handles both array format (Polymarket market channel) and dict format.

    Args:
        raw_message: Raw JSON string from WebSocket.
        db: Database instance for persistence.
    """
    try:
        data = json.loads(raw_message)
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON message", extra={"ctx_error": str(e)})
        return

    # Handle array format (Polymarket market channel sends arrays)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                await _route_single_message(item, db)
        return

    # Handle dict format
    await _route_single_message(data, db)


async def _route_single_message(data: dict[str, Any], db: Database) -> None:
    """Route a single message dict to appropriate handler."""
    # Polymarket uses event_type for market channel
    event_type = data.get("event_type", "").lower()
    msg_type = data.get("type", "").lower()
    channel = data.get("channel", "").lower()

    # Route based on event_type (Polymarket market channel format)
    if event_type == "book":
        await handle_orderbook_message(data, db)
    elif event_type == "price_change":
        # Price change updates orderbook
        await handle_orderbook_message(data, db)
    elif event_type == "last_trade_price":
        await handle_trade_message(data, db)
    elif event_type == "tick_size_change":
        logger.debug("Tick size change", extra={"ctx_data": str(data)[:200]})
    # Legacy format support
    elif msg_type == "trade" or channel == "trades":
        await handle_trade_message(data, db)
    elif msg_type in ("snapshot", "l2update") or channel == "l2":
        await handle_orderbook_message(data, db)
    elif msg_type == "pong":
        pass
    elif msg_type == "subscribed":
        logger.info(
            "Subscription confirmed",
            extra={
                "ctx_channel": data.get("channel"),
                "ctx_market": data.get("market"),
            },
        )
    elif msg_type == "error":
        logger.error(
            "WebSocket error from server",
            extra={"ctx_error": data.get("message", str(data))},
        )
    elif event_type or msg_type or channel:
        logger.debug(
            "Unhandled message",
            extra={"ctx_event_type": event_type, "ctx_type": msg_type, "ctx_channel": channel},
        )


class MessageRouter:
    """Configurable message router with custom handlers."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._handlers: dict[str, MessageHandler] = {
            "trade": handle_trade_message,
            "trades": handle_trade_message,
            "snapshot": handle_orderbook_message,
            "l2update": handle_orderbook_message,
            "l2": handle_orderbook_message,
        }
        self._custom_handlers: list[Callable[[dict], Coroutine[Any, Any, None]]] = []

    def add_handler(self, msg_type: str, handler: MessageHandler) -> None:
        """Register a custom handler for a message type."""
        self._handlers[msg_type.lower()] = handler

    def add_raw_handler(
        self, handler: Callable[[dict], Coroutine[Any, Any, None]]
    ) -> None:
        """Add a handler that receives all messages."""
        self._custom_handlers.append(handler)

    async def route(self, raw_message: str) -> None:
        """Route a message to appropriate handlers."""
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        # Call custom handlers
        for handler in self._custom_handlers:
            try:
                await handler(data)
            except Exception as e:
                logger.error("Custom handler error", extra={"ctx_error": str(e)})

        # Route to type-specific handlers
        msg_type = data.get("type", "").lower()
        channel = data.get("channel", "").lower()

        handler = self._handlers.get(msg_type) or self._handlers.get(channel)
        if handler:
            await handler(data, self._db)

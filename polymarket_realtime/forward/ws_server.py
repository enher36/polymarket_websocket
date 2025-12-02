"""Local WebSocket server for forwarding data to downstream consumers."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any, DefaultDict, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from polymarket_realtime.forward.event_bus import EventBus, ForwardEvent
from polymarket_realtime.utils.logging import get_logger

if TYPE_CHECKING:
    from polymarket_realtime.database.repository import Database
    from polymarket_realtime.websocket.manager import WebSocketManager

logger = get_logger(__name__)


class ForwardServer:
    """Local WebSocket server that forwards events to connected clients.

    Features:
    - Client subscription/unsubscription by token_id
    - Query active markets from database
    - Subscribe by category or all active markets
    - Automatic cleanup on client disconnect
    - Heartbeat/ping-pong via websockets library
    - Event bus integration for receiving upstream data

    Client Protocol:
    - Subscribe: {"action": "subscribe", "token_id": "<token_id>"} (legacy: "token")
    - Subscribe batch: {"action": "subscribe_batch", "token_ids": ["<id1>", "<id2>", ...]}
    - Unsubscribe: {"action": "unsubscribe", "token_id": "<token_id>"} (legacy: "token")
    - List markets: {"action": "list_markets", "category": "<optional>", "limit": 100}
    - Subscribe category: {"action": "subscribe_category", "category": "<category>"}
    - Subscribe all: {"action": "subscribe_all"}
    - Ping: {"action": "ping"}

    Response format uses "type" field (with "status" for backward compatibility).
    """

    def __init__(
        self,
        event_bus: EventBus,
        db: Optional[Database] = None,
        ws_manager: Optional[WebSocketManager] = None,
        host: str = "127.0.0.1",
        port: int = 8765,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
        market_limit: int = 500,
    ) -> None:
        """Initialize the forward server.

        Args:
            event_bus: Event bus to receive upstream events from.
            db: Database for querying markets (optional, enables list/subscribe features).
            ws_manager: WebSocket manager for upstream subscriptions (optional).
            host: Host to bind the server to.
            port: Port to listen on.
            ping_interval: Interval between ping messages.
            ping_timeout: Timeout for ping response.
            market_limit: Maximum markets to return in list_markets.
        """
        self._event_bus = event_bus
        self._db = db
        self._ws_manager = ws_manager
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._market_limit = market_limit

        # Token -> Set of connections subscribed to that token
        self._token_connections: DefaultDict[str, set[WebSocketServerProtocol]] = (
            defaultdict(set)
        )
        # Connection -> Set of tokens that connection subscribes to
        self._connection_tokens: dict[WebSocketServerProtocol, set[str]] = {}

        self._lock = asyncio.Lock()
        self._server: Optional[websockets.WebSocketServer] = None
        self._is_running = False

    async def start(self) -> None:
        """Start the WebSocket server."""
        if self._server:
            logger.warning("Forward server already running")
            return

        self._is_running = True
        self._server = await websockets.serve(
            self._handle_client,
            self._host,
            self._port,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
        )
        logger.info(
            "Forward server started",
            extra={"ctx_host": self._host, "ctx_port": self._port},
        )

    async def stop(self) -> None:
        """Stop the WebSocket server and cleanup."""
        self._is_running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        # Cleanup all subscriptions
        async with self._lock:
            tokens = list(self._token_connections.keys())
            self._token_connections.clear()
            self._connection_tokens.clear()

        # Unsubscribe from event bus
        for token in tokens:
            await self._event_bus.unsubscribe(token, self._on_event)

        logger.info("Forward server stopped")

    async def _handle_client(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a client connection lifecycle."""
        remote = str(websocket.remote_address) if websocket.remote_address else "unknown"
        logger.info("Client connected", extra={"ctx_remote": remote})

        # Initialize connection tracking
        async with self._lock:
            self._connection_tokens[websocket] = set()

        try:
            async for raw_message in websocket:
                if not self._is_running:
                    break
                await self._process_message(websocket, str(raw_message))
        except websockets.ConnectionClosed:
            logger.info("Client disconnected", extra={"ctx_remote": remote})
        except Exception as e:
            logger.error(
                "Client handler error",
                extra={"ctx_remote": remote, "ctx_error": str(e)},
            )
        finally:
            await self._cleanup_connection(websocket)

    def _get_token_id(self, message: dict[str, Any]) -> Optional[str]:
        """Extract token_id from message with backward compatibility.

        Accepts both 'token_id' (preferred) and 'token' (legacy) fields.

        Args:
            message: The parsed JSON message from client.

        Returns:
            The token_id string if valid, None otherwise.
        """
        token_id = message.get("token_id") or message.get("token")
        if token_id is None:
            return None
        token_id = str(token_id).strip()
        return token_id if token_id else None

    def _get_token_ids(self, message: dict[str, Any]) -> Optional[list[str]]:
        """Extract and validate token_ids list from message.

        Performs deduplication and filters empty/invalid entries.

        Args:
            message: The parsed JSON message from client.

        Returns:
            List of valid token_id strings, or None if token_ids field is missing/invalid.
        """
        token_ids = message.get("token_ids")
        if token_ids is None:
            return None
        if not isinstance(token_ids, list):
            return None

        seen: set[str] = set()
        result: list[str] = []
        for item in token_ids:
            if item is None:
                continue
            token_id = str(item).strip()
            if not token_id or token_id in seen:
                continue
            seen.add(token_id)
            result.append(token_id)
        return result

    async def _process_message(
        self, websocket: WebSocketServerProtocol, raw_message: str
    ) -> None:
        """Parse and handle client messages."""
        try:
            message: dict[str, Any] = json.loads(raw_message)
        except json.JSONDecodeError:
            await self._send_error(websocket, "invalid_json")
            return

        action = str(message.get("action", "")).lower()

        if action == "subscribe":
            token_id = self._get_token_id(message)
            if not token_id:
                await self._send_error(websocket, "invalid_token_id")
                return
            await self._add_subscription(websocket, token_id)
            await websocket.send(
                json.dumps({
                    "type": "subscribed",
                    "token_id": token_id,
                    # Backward compatibility
                    "status": "subscribed",
                    "token": token_id,
                })
            )

        elif action == "subscribe_batch":
            token_ids = self._get_token_ids(message)
            if token_ids is None:
                await self._send_error(websocket, "invalid_token_ids")
                return
            if not token_ids:
                await self._send_error(websocket, "empty_token_ids")
                return
            await self._subscribe_tokens_bulk(websocket, token_ids)
            await websocket.send(
                json.dumps({
                    "type": "subscribed_batch",
                    "token_ids": token_ids,
                    # Backward compatibility
                    "status": "subscribed_batch",
                })
            )

        elif action == "unsubscribe":
            token_id = self._get_token_id(message)
            if not token_id:
                await self._send_error(websocket, "invalid_token_id")
                return
            await self._remove_subscription(token_id, websocket)
            await websocket.send(
                json.dumps({
                    "type": "unsubscribed",
                    "token_id": token_id,
                    # Backward compatibility
                    "status": "unsubscribed",
                    "token": token_id,
                })
            )

        elif action == "list_markets":
            await self._handle_list_markets(websocket, message)

        elif action == "subscribe_category":
            await self._handle_subscribe_category(websocket, message)

        elif action == "subscribe_all":
            await self._handle_subscribe_category(websocket, {"category": None})

        elif action == "ping":
            await websocket.send(json.dumps({"type": "pong", "status": "pong"}))

        else:
            await self._send_error(websocket, "unsupported_action")

    async def _send_error(
        self, websocket: WebSocketServerProtocol, error: str
    ) -> None:
        """Send error response to client with unified format."""
        try:
            await websocket.send(
                json.dumps({
                    "type": "error",
                    "error": error,
                    # Backward compatibility
                    "status": "error",
                })
            )
        except websockets.ConnectionClosed:
            pass

    async def _handle_list_markets(
        self, websocket: WebSocketServerProtocol, message: dict[str, Any]
    ) -> None:
        """Handle list_markets request.

        Returns active markets from the database.

        Request: {"action": "list_markets", "category": "<optional>", "limit": 100}
        Response: {"status": "markets", "category": ..., "count": N, "markets": [...]}
        """
        if not self._db:
            await self._send_error(websocket, "database_unavailable")
            return

        category = message.get("category")
        # Safe limit parsing with validation
        try:
            raw_limit = message.get("limit", self._market_limit)
            limit = max(1, min(int(raw_limit), self._market_limit))
        except (TypeError, ValueError):
            limit = self._market_limit

        try:
            markets = await self._db.list_active_markets(
                category=category, limit=limit
            )
            # Enrich each market with its token_ids
            enriched_markets = []
            for market in markets:
                token_rows = await self._db.get_token_ids_by_market(market["id"])
                tokens = [
                    {"token_id": token_id, "outcome": outcome}
                    for token_id, outcome in token_rows
                ]
                enriched_markets.append({
                    "id": market["id"],
                    "slug": market.get("slug", ""),
                    "question": market.get("question", ""),
                    "category": market.get("category", ""),
                    "tokens": tokens,
                })

            await websocket.send(
                json.dumps({
                    "status": "markets",
                    "category": category,
                    "count": len(enriched_markets),
                    "limit": limit,
                    "max_limit": self._market_limit,
                    "markets": enriched_markets,
                })
            )
            logger.info(
                "Sent market list",
                extra={"ctx_category": category, "ctx_count": len(enriched_markets)},
            )
        except Exception as e:
            logger.error(
                "Failed to list markets",
                extra={"ctx_error": str(e), "ctx_category": category},
            )
            await self._send_error(websocket, "list_markets_failed")

    async def _handle_subscribe_category(
        self, websocket: WebSocketServerProtocol, message: dict[str, Any]
    ) -> None:
        """Handle subscribe_category or subscribe_all request.

        Subscribes to all tokens in the specified category (or all if None).

        Request: {"action": "subscribe_category", "category": "<category>", "limit": 100}
        Response: {"status": "subscribed_category", "category": ..., "token_count": N}
        """
        if not self._db:
            await self._send_error(websocket, "database_unavailable")
            return

        category = message.get("category")
        # Safe limit parsing with validation
        try:
            raw_limit = message.get("limit", self._market_limit)
            limit = max(1, min(int(raw_limit), self._market_limit))
        except (TypeError, ValueError):
            limit = self._market_limit

        try:
            markets = await self._db.list_active_markets(
                category=category, limit=limit
            )
            # Collect all token_ids
            token_ids: list[str] = []
            for market in markets:
                token_rows = await self._db.get_token_ids_by_market(market["id"])
                token_ids.extend(token_id for token_id, _ in token_rows)

            # Deduplicate
            unique_tokens = list(dict.fromkeys(token_ids))

            # Subscribe this connection to all tokens
            new_subscriptions = await self._subscribe_tokens_bulk(websocket, unique_tokens)

            await websocket.send(
                json.dumps({
                    "status": "subscribed_category",
                    "category": category,
                    "market_count": len(markets),
                    "token_count": len(unique_tokens),
                    "new_subscriptions": len(new_subscriptions),
                    "limit": limit,
                    "max_limit": self._market_limit,
                })
            )
            logger.info(
                "Category subscription completed",
                extra={
                    "ctx_category": category,
                    "ctx_markets": len(markets),
                    "ctx_tokens": len(unique_tokens),
                    "ctx_new": len(new_subscriptions),
                },
            )
        except Exception as e:
            logger.error(
                "Failed to subscribe category",
                extra={"ctx_error": str(e), "ctx_category": category},
            )
            await self._send_error(websocket, "subscribe_category_failed")

    async def _subscribe_tokens_bulk(
        self, websocket: WebSocketServerProtocol, token_ids: list[str]
    ) -> list[str]:
        """Subscribe a connection to multiple tokens with deduplication.

        Registers with EventBus and optionally triggers upstream subscriptions.

        Args:
            websocket: The client connection.
            token_ids: List of token IDs to subscribe to.

        Returns:
            List of token IDs that were newly registered (first subscriber).
        """
        new_tokens: list[str] = []

        async with self._lock:
            for token_id in token_ids:
                connections = self._token_connections[token_id]
                is_first_subscriber = not connections
                connections.add(websocket)
                self._connection_tokens.setdefault(websocket, set()).add(token_id)
                if is_first_subscriber:
                    new_tokens.append(token_id)

        # Register with EventBus for new tokens
        for token_id in new_tokens:
            await self._event_bus.subscribe(token_id, self._on_event)

        # Trigger upstream subscription if ws_manager is available
        if self._ws_manager and new_tokens:
            for token_id in new_tokens:
                await self._ws_manager.subscribe(token_id)
                await asyncio.sleep(0.05)  # Rate limiting to avoid overwhelming upstream

        if new_tokens:
            logger.info(
                "Bulk subscription registered",
                extra={"ctx_new_tokens": len(new_tokens), "ctx_total": len(token_ids)},
            )

        return new_tokens

    async def _add_subscription(
        self, websocket: WebSocketServerProtocol, token_id: str
    ) -> None:
        """Add client subscription to a token."""
        register_with_bus = False

        async with self._lock:
            connections = self._token_connections[token_id]
            if not connections:
                register_with_bus = True
            connections.add(websocket)
            self._connection_tokens.setdefault(websocket, set()).add(token_id)

        if register_with_bus:
            await self._event_bus.subscribe(token_id, self._on_event)
            # Trigger upstream subscription if ws_manager is available
            if self._ws_manager:
                await self._ws_manager.subscribe(token_id)
            logger.info(
                "Forward subscription registered",
                extra={"ctx_token_id": token_id[:30]},
            )

    async def _remove_subscription(
        self, token_id: str, websocket: WebSocketServerProtocol
    ) -> None:
        """Remove client subscription from a token."""
        should_unsubscribe = False

        async with self._lock:
            connections = self._token_connections.get(token_id)
            if connections:
                connections.discard(websocket)
                if not connections:
                    self._token_connections.pop(token_id, None)
                    should_unsubscribe = True

            tokens = self._connection_tokens.get(websocket)
            if tokens:
                tokens.discard(token_id)

        if should_unsubscribe:
            await self._event_bus.unsubscribe(token_id, self._on_event)
            logger.info(
                "Forward subscription released",
                extra={"ctx_token_id": token_id[:30]},
            )

    async def _cleanup_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Cleanup when a client disconnects."""
        async with self._lock:
            tokens = self._connection_tokens.pop(websocket, set())

        for token_id in tokens:
            await self._remove_subscription(token_id, websocket)

    async def _on_event(self, event: ForwardEvent) -> None:
        """Handle events from the event bus and forward to clients."""
        async with self._lock:
            connections = list(self._token_connections.get(event.token_id, set()))

        if not connections:
            return

        # Build message
        message = json.dumps({
            "type": event.event_type,
            "token_id": event.token_id,
            "data": event.payload,
            "timestamp": event.timestamp.isoformat(),
        })

        # Send to all subscribed clients
        await asyncio.gather(
            *(self._send_safe(conn, message) for conn in connections),
            return_exceptions=True,
        )

    async def _send_safe(
        self, websocket: WebSocketServerProtocol, message: str
    ) -> None:
        """Send message to client, handling disconnection gracefully."""
        try:
            await websocket.send(message)
        except websockets.ConnectionClosed:
            await self._cleanup_connection(websocket)

    @property
    def client_count(self) -> int:
        """Get number of connected clients."""
        return len(self._connection_tokens)

    @property
    def subscription_count(self) -> int:
        """Get total number of subscriptions across all clients."""
        return sum(len(tokens) for tokens in self._connection_tokens.values())


__all__ = ["ForwardServer"]

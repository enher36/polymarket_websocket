"""Local WebSocket server for forwarding data to downstream consumers."""

import asyncio
import json
from collections import defaultdict
from typing import Any, DefaultDict, Optional

import websockets
from websockets.server import WebSocketServerProtocol

from polymarket_realtime.forward.event_bus import EventBus, ForwardEvent
from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)


class ForwardServer:
    """Local WebSocket server that forwards events to connected clients.

    Features:
    - Client subscription/unsubscription by token_id
    - Automatic cleanup on client disconnect
    - Heartbeat/ping-pong via websockets library
    - Event bus integration for receiving upstream data

    Client Protocol:
    - Subscribe: {"action": "subscribe", "token": "<token_id>"}
    - Unsubscribe: {"action": "unsubscribe", "token": "<token_id>"}
    - Response: {"status": "subscribed/unsubscribed", "token": "<token_id>"}
    """

    def __init__(
        self,
        event_bus: EventBus,
        host: str = "127.0.0.1",
        port: int = 8765,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ) -> None:
        """Initialize the forward server.

        Args:
            event_bus: Event bus to receive upstream events from.
            host: Host to bind the server to.
            port: Port to listen on.
            ping_interval: Interval between ping messages.
            ping_timeout: Timeout for ping response.
        """
        self._event_bus = event_bus
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout

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
        token = message.get("token")

        if action == "subscribe" and token:
            await self._add_subscription(websocket, str(token))
            await websocket.send(
                json.dumps({"status": "subscribed", "token": token})
            )

        elif action == "unsubscribe" and token:
            await self._remove_subscription(str(token), websocket)
            await websocket.send(
                json.dumps({"status": "unsubscribed", "token": token})
            )

        elif action == "ping":
            await websocket.send(json.dumps({"status": "pong"}))

        else:
            await self._send_error(websocket, "unsupported_action")

    async def _send_error(
        self, websocket: WebSocketServerProtocol, error: str
    ) -> None:
        """Send error response to client."""
        try:
            await websocket.send(json.dumps({"error": error}))
        except websockets.ConnectionClosed:
            pass

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

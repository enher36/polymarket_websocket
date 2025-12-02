"""WebSocket connection manager for Polymarket real-time data."""

import asyncio
import json
from typing import Callable, Coroutine, Optional, Any

import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

from polymarket_realtime.database.repository import Database
from polymarket_realtime.utils.logging import get_logger
from polymarket_realtime.websocket.handlers import (
    prune_orderbook_state,
    reset_orderbook_state,
    route_message,
)

logger = get_logger(__name__)


class WebSocketManager:
    """Manages WebSocket connection to Polymarket CLOB.

    Features:
    - Automatic reconnection with exponential backoff
    - Subscription management
    - Heartbeat/ping-pong handling
    - Graceful shutdown
    """

    def __init__(
        self,
        url: str,
        db: Database,
        heartbeat_interval: float = 15.0,
        reconnect_delay: float = 5.0,
        max_reconnect_delay: float = 60.0,
        message_handler: Optional[
            Callable[[str, Database], Coroutine[Any, Any, None]]
        ] = None,
    ) -> None:
        """Initialize WebSocket manager.

        Args:
            url: WebSocket endpoint URL.
            db: Database for data persistence.
            heartbeat_interval: Seconds between heartbeat pings.
            reconnect_delay: Initial reconnect delay in seconds.
            max_reconnect_delay: Maximum reconnect delay.
            message_handler: Custom message handler (defaults to route_message).
        """
        self.url = url
        self._db = db
        self._heartbeat_interval = heartbeat_interval
        self._reconnect_delay = reconnect_delay
        self._max_reconnect_delay = max_reconnect_delay
        self._message_handler = message_handler or route_message

        # Connection state
        self._ws: Optional[WebSocketClientProtocol] = None
        self._is_running = False
        self._current_reconnect_delay = reconnect_delay

        # Subscription registry: token_id -> set of channels
        self._subscriptions: dict[str, set[str]] = {}

        # Tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._receive_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_connect_callbacks: list[Callable[[], Coroutine[Any, Any, None]]] = []
        self._on_disconnect_callbacks: list[Callable[[], Coroutine[Any, Any, None]]] = []

    # ==================== Subscription Management ====================

    async def subscribe(self, token_id: str, channels: set[str] | None = None) -> None:
        """Register and activate a subscription.

        If WebSocket is connected, sends subscription message immediately.

        Args:
            token_id: Token ID to subscribe to.
            channels: Set of channels ("l2", "trades"). Defaults to both.
        """
        if channels is None:
            channels = {"l2", "trades"}
        self._subscriptions[token_id] = channels
        logger.info(
            "Subscription registered",
            extra={"ctx_token_id": token_id, "ctx_channels": list(channels)},
        )

        # Send immediately if connected
        if self.is_connected:
            for channel in channels:
                await self._send_subscription(token_id, channel)

    async def unsubscribe(self, token_id: str) -> None:
        """Remove a subscription and notify server.

        Args:
            token_id: Token ID to unsubscribe from.
        """
        channels = self._subscriptions.pop(token_id, None)
        # Clean up orderbook tracking state for this token
        reset_orderbook_state(token_id)

        # Send unsubscribe if connected
        if channels and self.is_connected:
            for channel in channels:
                await self._send_unsubscription(token_id, channel)

        logger.info("Subscription removed", extra={"ctx_token_id": token_id})

    async def subscribe_multiple(self, token_ids: list[str], channels: set[str] | None = None) -> None:
        """Register multiple subscriptions."""
        for token_id in token_ids:
            await self.subscribe(token_id, channels)

    async def _send_subscription(self, token_id: str, channel: str) -> None:
        """Send subscription message to server.

        Polymarket market channel uses assets_ids format with type field.
        """
        if not self._ws:
            return

        # Polymarket market channel format (requires type: "market")
        message = json.dumps({
            "assets_ids": [token_id],
            "type": "market",
        })
        await self._ws.send(message)
        logger.debug(
            "Sent subscription",
            extra={"ctx_token_id": token_id[:30], "ctx_channel": channel},
        )

    async def _send_unsubscription(self, token_id: str, channel: str) -> None:
        """Send unsubscription message to server."""
        if not self._ws:
            return
        # Note: Polymarket may not support explicit unsubscribe
        # Just remove from local registry
        logger.debug("Unsubscription requested", extra={"ctx_token_id": token_id[:30]})

    async def _resubscribe_all(self) -> None:
        """Resubscribe to all registered subscriptions."""
        for token_id, channels in self._subscriptions.items():
            for channel in channels:
                await self._send_subscription(token_id, channel)
                await asyncio.sleep(0.05)  # Small delay to avoid overwhelming server

    # ==================== Connection Management ====================

    async def connect(self) -> bool:
        """Establish WebSocket connection.

        Returns:
            True if connection successful.
        """
        try:
            self._ws = await websockets.connect(
                self.url,
                ping_interval=None,  # We handle our own heartbeat
                ping_timeout=None,
            )
            logger.info("WebSocket connected", extra={"ctx_url": self.url})

            # Reset reconnect delay on successful connection
            self._current_reconnect_delay = self._reconnect_delay

            # Notify callbacks
            for callback in self._on_connect_callbacks:
                try:
                    await callback()
                except Exception as e:
                    logger.error("Connect callback error", extra={"ctx_error": str(e)})

            return True

        except Exception as e:
            logger.error(
                "WebSocket connection failed",
                extra={"ctx_url": self.url, "ctx_error": str(e)},
            )
            return False

    async def disconnect(self) -> None:
        """Close WebSocket connection gracefully."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        # Notify callbacks
        for callback in self._on_disconnect_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.error("Disconnect callback error", extra={"ctx_error": str(e)})

        logger.info("WebSocket disconnected")

    # ==================== Heartbeat ====================

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat pings.

        On failure, closes the connection to trigger reconnection.
        Also triggers orderbook state pruning to handle low-volume scenarios.
        """
        while self._is_running and self._ws:
            try:
                # Polymarket expects plain "PING" string, not JSON
                await self._ws.send("PING")
                logger.debug("Sent heartbeat ping")
                # Prune stale orderbook state during heartbeat
                # This ensures TTL-based cleanup works even in low-volume scenarios
                prune_orderbook_state()
            except Exception as e:
                logger.warning("Heartbeat failed, triggering reconnect", extra={"ctx_error": str(e)})
                # Close the connection to trigger reconnection
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                break
            await asyncio.sleep(self._heartbeat_interval)

    # ==================== Message Handling ====================

    async def _receive_loop(self) -> None:
        """Receive and process messages."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if not self._is_running:
                    break
                try:
                    await self._message_handler(message, self._db)
                except Exception as e:
                    logger.error(
                        "Message handler error",
                        extra={"ctx_error": str(e)},
                    )
        except ConnectionClosed as e:
            logger.warning(
                "WebSocket connection closed",
                extra={"ctx_code": e.code, "ctx_reason": e.reason},
            )

    # ==================== Main Run Loop ====================

    async def run(self) -> None:
        """Start the WebSocket manager.

        This is the main entry point. It handles:
        - Initial connection
        - Reconnection on failure
        - Subscription management
        - Message processing
        """
        self._is_running = True
        logger.info("WebSocket manager starting")

        while self._is_running:
            # Connect
            connected = await self.connect()
            if not connected:
                await self._wait_before_reconnect()
                continue

            # Resubscribe
            await self._resubscribe_all()

            # Start heartbeat
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Process messages until disconnection
            await self._receive_loop()

            # Cleanup
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass

            await self.disconnect()

            if self._is_running:
                await self._wait_before_reconnect()

        logger.info("WebSocket manager stopped")

    async def _wait_before_reconnect(self) -> None:
        """Wait before attempting reconnection with exponential backoff."""
        logger.info(
            "Waiting before reconnect",
            extra={"ctx_delay": self._current_reconnect_delay},
        )
        await asyncio.sleep(self._current_reconnect_delay)

        # Exponential backoff
        self._current_reconnect_delay = min(
            self._current_reconnect_delay * 2,
            self._max_reconnect_delay,
        )

    async def stop(self) -> None:
        """Stop the WebSocket manager gracefully."""
        logger.info("Stopping WebSocket manager")
        self._is_running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        await self.disconnect()

        # Clear all orderbook tracking state to prevent memory leak
        reset_orderbook_state()

    # ==================== Callbacks ====================

    def on_connect(self, callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for connection events."""
        self._on_connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register a callback for disconnection events."""
        self._on_disconnect_callbacks.append(callback)

    # ==================== Properties ====================

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is currently connected."""
        # websockets 15.x uses state instead of open
        return self._ws is not None and self._ws.state.name == "OPEN"

    @property
    def subscription_count(self) -> int:
        """Get number of active subscriptions."""
        return len(self._subscriptions)

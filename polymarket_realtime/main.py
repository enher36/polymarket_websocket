"""Main entry point for Polymarket real-time data fetcher."""

import asyncio
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

from polymarket_realtime.api.client import PolymarketClient
from polymarket_realtime.config import get_settings
from polymarket_realtime.database.repository import Database
from polymarket_realtime.forward.event_bus import event_bus
from polymarket_realtime.forward.ws_server import ForwardServer
from polymarket_realtime.services.market_scanner import MarketScanner
from polymarket_realtime.services.url_resolver import UrlResolver
from polymarket_realtime.utils.logging import get_logger, setup_logging
from polymarket_realtime.web.server import WebServer
from polymarket_realtime.web.stats import StatsCollector
from polymarket_realtime.websocket.manager import WebSocketManager

logger = get_logger(__name__)


class Application:
    """Main application orchestrator.

    Coordinates all components:
    - API client
    - Database
    - Market scanner
    - WebSocket manager
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._db: Optional[Database] = None
        self._client: Optional[PolymarketClient] = None
        self._scanner: Optional[MarketScanner] = None
        self._ws_manager: Optional[WebSocketManager] = None
        self._url_resolver: Optional[UrlResolver] = None
        self._forward_server: Optional[ForwardServer] = None
        self._stats_collector: Optional[StatsCollector] = None
        self._web_server: Optional[WebServer] = None
        self._start_time = datetime.now(timezone.utc)
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing application")

        # Database
        self._db = Database(self._settings.db_path)
        await self._db.initialize()

        # API Client
        self._client = PolymarketClient(
            base_url=self._settings.api_url,
            timeout=self._settings.http_timeout,
            rate_limit_per_sec=self._settings.http_rps,
        )

        # Services
        self._scanner = MarketScanner(self._client, self._db)
        self._url_resolver = UrlResolver(self._client, self._db)

        # WebSocket Manager
        self._ws_manager = WebSocketManager(
            url=self._settings.ws_url,
            db=self._db,
            heartbeat_interval=self._settings.ws_heartbeat_sec,
            reconnect_delay=self._settings.ws_reconnect_sec,
        )

        # Forward Server (optional - graceful degradation if startup fails)
        if self._settings.forward_enabled:
            self._forward_server = ForwardServer(
                event_bus=event_bus,
                db=self._db,
                ws_manager=self._ws_manager,
                host=self._settings.forward_host,
                port=self._settings.forward_port,
            )
            try:
                await self._forward_server.start()
                logger.info(
                    "Forward server started",
                    extra={
                        "ctx_host": self._settings.forward_host,
                        "ctx_port": self._settings.forward_port,
                    },
                )
            except Exception as e:
                logger.error(
                    "Failed to start forward server - continuing without forwarding",
                    extra={
                        "ctx_error": str(e),
                        "ctx_error_type": type(e).__name__,
                    },
                )
                self._forward_server = None

        # Stats collector for metrics aggregation
        self._stats_collector = StatsCollector(
            ws_manager=self._ws_manager,
            forward_server=self._forward_server,
            db=self._db,
            start_time=self._start_time,
        )

        # Web monitoring server (optional)
        if self._settings.web_enabled:
            self._web_server = WebServer(
                stats_collector=self._stats_collector,
                host=self._settings.web_host,
                port=self._settings.web_port,
            )
            try:
                await self._web_server.start()
                logger.info(
                    "Web monitoring server started",
                    extra={
                        "ctx_host": self._settings.web_host,
                        "ctx_port": self._settings.web_port,
                    },
                )
            except Exception as e:
                logger.error(
                    "Failed to start web server - continuing without monitoring UI",
                    extra={
                        "ctx_error": str(e),
                        "ctx_error_type": type(e).__name__,
                    },
                )
                self._web_server = None

        logger.info("Application initialized")

    async def shutdown(self) -> None:
        """Graceful shutdown of all components."""
        logger.info("Shutting down application")

        if self._scanner:
            await self._scanner.stop_periodic_scan()

        if self._web_server:
            await self._web_server.stop()

        if self._ws_manager:
            await self._ws_manager.stop()

        if self._forward_server:
            await self._forward_server.stop()

        if self._client:
            await self._client.close()

        if self._db:
            await self._db.close()

        logger.info("Application shutdown complete")

    async def _subscribe_cached_markets(self) -> int:
        """Subscribe to markets from database cache.

        Returns:
            Number of tokens subscribed.
        """
        if not self._db or not self._ws_manager:
            return 0

        try:
            markets = await self._db.list_active_markets(
                category=self._settings.category,
                limit=10,
            )
        except Exception as e:
            logger.warning(
                "Failed to load cached markets",
                extra={"ctx_error": str(e)},
            )
            return 0

        if not markets:
            logger.info("No cached markets for initial subscription")
            return 0

        subscribed = 0
        for market in markets:
            try:
                tokens = await self._db.get_token_ids_by_market(market["id"])
                for token_id, _ in tokens:
                    await self._ws_manager.subscribe(token_id)
                    subscribed += 1
            except Exception as e:
                logger.warning(
                    "Failed to subscribe cached market",
                    extra={"ctx_market_id": market["id"], "ctx_error": str(e)},
                )

        logger.info(
            "Subscribed to cached markets",
            extra={"ctx_markets": len(markets), "ctx_tokens": subscribed},
        )
        return subscribed

    def _on_ws_task_done(self, task: asyncio.Task) -> None:
        """Handle WebSocket task completion."""
        try:
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "WebSocket manager stopped unexpectedly",
                    extra={"ctx_error": str(exc), "ctx_error_type": type(exc).__name__},
                )
                self.request_shutdown()
        except asyncio.CancelledError:
            pass

    async def run(self) -> None:
        """Run the application.

        Optimized startup flow:
        1. Initialize all components
        2. Start WebSocket immediately (non-blocking)
        3. Subscribe to cached markets from DB
        4. Start periodic scanning (first iteration serves as initial scan)
        5. Wait for shutdown
        """
        await self.initialize()

        # Guard: ensure critical components are initialized
        if not self._ws_manager:
            logger.error("WebSocket manager not initialized, cannot run")
            return
        if not self._scanner:
            logger.error("Market scanner not initialized, cannot run")
            return

        # Start WebSocket manager immediately (non-blocking)
        ws_task = asyncio.create_task(self._ws_manager.run())
        ws_task.add_done_callback(self._on_ws_task_done)
        logger.info("WebSocket manager started")

        # Subscribe to cached markets first (fast startup)
        await self._subscribe_cached_markets()

        # Start periodic scanning (first iteration acts as initial scan)
        logger.info("Starting background market scanning")
        await self._scanner.start_periodic_scan(
            interval_seconds=self._settings.scan_interval_sec,
            category=self._settings.category,
        )

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cancel WebSocket task
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass

    def request_shutdown(self) -> None:
        """Request application shutdown."""
        self._shutdown_event.set()


# Global application instance for signal handlers
_app: Optional[Application] = None


def _handle_signal(signum: int, frame) -> None:
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, initiating shutdown")
    if _app:
        _app.request_shutdown()


async def async_main() -> None:
    """Async main entry point."""
    global _app

    settings = get_settings()
    setup_logging(settings.log_level)

    logger.info(
        "Starting Polymarket real-time fetcher",
        extra={
            "ctx_api_url": settings.api_url,
            "ctx_ws_url": settings.ws_url,
            "ctx_db_path": settings.db_path,
            "ctx_forward_enabled": settings.forward_enabled,
            "ctx_forward_addr": f"{settings.forward_host}:{settings.forward_port}"
            if settings.forward_enabled
            else "disabled",
            "ctx_web_addr": f"http://{settings.web_host}:{settings.web_port}"
            if settings.web_enabled
            else "disabled",
        },
    )

    _app = Application()

    # Setup signal handlers
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        await _app.run()
    finally:
        await _app.shutdown()


def cli_main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    cli_main()

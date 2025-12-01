"""Main entry point for Polymarket real-time data fetcher."""

import asyncio
import signal
import sys
from typing import Optional

from polymarket_realtime.api.client import PolymarketClient
from polymarket_realtime.config import get_settings
from polymarket_realtime.database.repository import Database
from polymarket_realtime.forward.event_bus import event_bus
from polymarket_realtime.forward.ws_server import ForwardServer
from polymarket_realtime.services.market_scanner import MarketScanner
from polymarket_realtime.services.url_resolver import UrlResolver
from polymarket_realtime.utils.logging import get_logger, setup_logging
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

        logger.info("Application initialized")

    async def shutdown(self) -> None:
        """Graceful shutdown of all components."""
        logger.info("Shutting down application")

        if self._scanner:
            await self._scanner.stop_periodic_scan()

        if self._ws_manager:
            await self._ws_manager.stop()

        if self._forward_server:
            await self._forward_server.stop()

        if self._client:
            await self._client.close()

        if self._db:
            await self._db.close()

        logger.info("Application shutdown complete")

    async def run(self) -> None:
        """Run the application."""
        await self.initialize()

        # Initial market scan
        logger.info("Running initial market scan")
        result = await self._scanner.scan_all(category=self._settings.category)
        logger.info(
            "Initial scan complete",
            extra={"ctx_markets": result.total_count},
        )

        # Start periodic scanning
        await self._scanner.start_periodic_scan(
            interval_seconds=self._settings.scan_interval_sec,
            category=self._settings.category,
        )

        # Example: Subscribe to some markets from database
        markets = await self._db.list_active_markets(
            category=self._settings.category,
            limit=10,
        )
        for market in markets:
            tokens = await self._db.get_token_ids_by_market(market["id"])
            for token_id, _ in tokens:
                await self._ws_manager.subscribe(token_id)

        # Run WebSocket manager (blocks until shutdown)
        ws_task = asyncio.create_task(self._ws_manager.run())

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

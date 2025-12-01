"""Lightweight aiohttp server for monitoring dashboard and API endpoints.

This module provides an HTTP server that exposes:
- /api/health - Health check endpoint
- /api/metrics - Full metrics snapshot
- / - Static dashboard page
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from aiohttp import web

from polymarket_realtime.utils.logging import get_logger
from polymarket_realtime.web.stats import StatsCollector

logger = get_logger(__name__)


class WebServer:
    """HTTP server hosting monitoring APIs and the dashboard UI.

    This server provides REST API endpoints for health checks and metrics,
    as well as serving the static HTML dashboard page. It integrates with
    the StatsCollector to retrieve live metrics from application components.

    Example:
        server = WebServer(
            stats_collector=stats_collector,
            host="127.0.0.1",
            port=8080,
        )
        await server.start()
        # ... application runs ...
        await server.stop()
    """

    def __init__(
        self,
        stats_collector: StatsCollector,
        host: str = "127.0.0.1",
        port: int = 8080,
        static_dir: Optional[Path] = None,
    ) -> None:
        """Initialize the web server.

        Args:
            stats_collector: Collector for aggregating metrics from components.
            host: Host address to bind the server to.
            port: Port number to listen on.
            static_dir: Directory containing static files (default: ./static).
        """
        self._stats_collector = stats_collector
        self._host = host
        self._port = port
        self._static_dir = static_dir or Path(__file__).parent / "static"
        self._index_path = self._static_dir / "index.html"

        self._app = web.Application()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

        self._register_routes()

    def _register_routes(self) -> None:
        """Register all HTTP routes."""
        self._app.router.add_get("/api/health", self._handle_health)
        self._app.router.add_get("/api/metrics", self._handle_metrics)
        self._app.router.add_get("/", self._handle_index)

        if self._static_dir.exists():
            self._app.router.add_static(
                "/static",
                str(self._static_dir),
                show_index=False,
            )

    async def start(self) -> None:
        """Start the HTTP server.

        Raises:
            RuntimeError: If server is already running.
            OSError: If the port is already in use.
        """
        if self._runner is not None:
            logger.warning("Web server already running")
            return

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        logger.info(
            "Web server started",
            extra={"ctx_host": self._host, "ctx_port": self._port},
        )

    async def stop(self) -> None:
        """Stop the HTTP server and cleanup resources."""
        if self._site is not None:
            await self._site.stop()
            self._site = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        logger.info("Web server stopped")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Handle GET /api/health - Health check endpoint.

        Returns a JSON response with:
        - status: "ok" if upstream is connected, "degraded" otherwise
        - upstream_connected: boolean connection status
        - downstream_clients: number of connected clients
        - uptime_seconds: server uptime
        """
        try:
            metrics = await self._stats_collector.collect()
            status = "ok" if metrics.upstream.connected else "degraded"

            payload = {
                "status": status,
                "upstream_connected": metrics.upstream.connected,
                "downstream_clients": metrics.downstream.clients,
                "uptime_seconds": round(metrics.uptime_seconds, 1),
            }
            return web.json_response(payload)
        except Exception as e:
            logger.error(
                "Health check failed",
                extra={"ctx_error": str(e)},
            )
            return web.json_response(
                {"status": "error", "message": str(e)},
                status=500,
            )

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        """Handle GET /api/metrics - Full metrics snapshot.

        Returns a JSON response with complete system metrics including
        upstream connection, downstream clients, and market statistics.
        """
        try:
            metrics = await self._stats_collector.collect_dict()
            return web.json_response(metrics)
        except Exception as e:
            logger.error(
                "Metrics collection failed",
                extra={"ctx_error": str(e)},
            )
            return web.json_response(
                {"error": "metrics_collection_failed", "message": str(e)},
                status=500,
            )

    async def _handle_index(self, request: web.Request) -> web.StreamResponse:
        """Handle GET / - Serve the dashboard HTML page."""
        if not self._index_path.exists():
            return web.Response(
                status=404,
                text="Dashboard page not found. Please ensure index.html exists.",
            )

        return web.FileResponse(path=self._index_path)

    @property
    def host(self) -> str:
        """Get the configured host address."""
        return self._host

    @property
    def port(self) -> int:
        """Get the configured port number."""
        return self._port

    @property
    def is_running(self) -> bool:
        """Check if the server is currently running."""
        return self._runner is not None


__all__ = ["WebServer"]

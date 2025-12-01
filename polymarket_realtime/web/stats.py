"""Runtime metrics collection for the monitoring dashboard.

This module provides a StatsCollector class that aggregates metrics from
various application components for display in the web UI and API endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from polymarket_realtime.utils.logging import get_logger

if TYPE_CHECKING:
    from polymarket_realtime.database.repository import Database
    from polymarket_realtime.forward.ws_server import ForwardServer
    from polymarket_realtime.websocket.manager import WebSocketManager

logger = get_logger(__name__)


@dataclass(frozen=True)
class UpstreamMetrics:
    """Metrics for upstream Polymarket WebSocket connection."""

    connected: bool
    subscriptions: int


@dataclass(frozen=True)
class DownstreamMetrics:
    """Metrics for downstream forwarding server."""

    enabled: bool
    clients: int
    subscriptions: int


@dataclass(frozen=True)
class MarketMetrics:
    """Metrics for market data in database."""

    active_count: int


@dataclass(frozen=True)
class SystemMetrics:
    """System-level metrics."""

    timestamp: datetime
    uptime_seconds: float
    upstream: UpstreamMetrics
    downstream: DownstreamMetrics
    markets: MarketMetrics

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to a JSON-serializable dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "uptime_seconds": round(self.uptime_seconds, 1),
            "upstream": {
                "connected": self.upstream.connected,
                "subscriptions": self.upstream.subscriptions,
            },
            "downstream": {
                "enabled": self.downstream.enabled,
                "clients": self.downstream.clients,
                "subscriptions": self.downstream.subscriptions,
            },
            "markets": {
                "active_count": self.markets.active_count,
            },
        }


class StatsCollector:
    """Aggregates runtime metrics from core application components.

    This class provides a centralized interface for collecting metrics
    from WebSocketManager, ForwardServer, and Database components.
    It is designed to be injected with live instances and queried
    by the web server for API responses.

    Example:
        collector = StatsCollector(
            ws_manager=ws_manager,
            forward_server=forward_server,
            db=db,
            start_time=datetime.now(timezone.utc),
        )
        metrics = await collector.collect()
    """

    def __init__(
        self,
        ws_manager: WebSocketManager,
        forward_server: Optional[ForwardServer],
        db: Database,
        start_time: datetime,
    ) -> None:
        """Initialize the stats collector.

        Args:
            ws_manager: WebSocket manager for upstream connection metrics.
            forward_server: Forward server for downstream client metrics (optional).
            db: Database repository for market data queries.
            start_time: Application start time for uptime calculation.
        """
        self._ws_manager = ws_manager
        self._forward_server = forward_server
        self._db = db
        self._start_time = start_time

    async def _get_active_market_count(self) -> int:
        """Get count of active markets from database.

        Uses optimized COUNT(*) query for performance.
        """
        try:
            return await self._db.count_active_markets()
        except Exception as e:
            logger.error(
                "Failed to get active market count",
                extra={"ctx_error": str(e)},
            )
            return 0

    def _get_upstream_metrics(self) -> UpstreamMetrics:
        """Collect metrics from upstream WebSocket connection."""
        return UpstreamMetrics(
            connected=self._ws_manager.is_connected,
            subscriptions=self._ws_manager.subscription_count,
        )

    def _get_downstream_metrics(self) -> DownstreamMetrics:
        """Collect metrics from downstream forwarding server."""
        if self._forward_server is None:
            return DownstreamMetrics(
                enabled=False,
                clients=0,
                subscriptions=0,
            )

        return DownstreamMetrics(
            enabled=True,
            clients=self._forward_server.client_count,
            subscriptions=self._forward_server.subscription_count,
        )

    async def collect(self) -> SystemMetrics:
        """Collect all metrics and return as a SystemMetrics dataclass.

        Returns:
            SystemMetrics containing all current application metrics.
        """
        now = datetime.now(timezone.utc)
        uptime = (now - self._start_time).total_seconds()
        active_markets = await self._get_active_market_count()

        return SystemMetrics(
            timestamp=now,
            uptime_seconds=uptime,
            upstream=self._get_upstream_metrics(),
            downstream=self._get_downstream_metrics(),
            markets=MarketMetrics(active_count=active_markets),
        )

    async def collect_dict(self) -> dict[str, Any]:
        """Collect metrics as a JSON-serializable dictionary.

        Returns:
            Dictionary representation of all metrics.
        """
        metrics = await self.collect()
        return metrics.to_dict()


__all__ = [
    "StatsCollector",
    "SystemMetrics",
    "UpstreamMetrics",
    "DownstreamMetrics",
    "MarketMetrics",
]

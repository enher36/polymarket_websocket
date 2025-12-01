"""Market scanner service for discovering and tracking active markets."""

import asyncio
from datetime import datetime, timezone
from typing import Optional

from polymarket_realtime.api.client import PolymarketClient
from polymarket_realtime.database.repository import Database
from polymarket_realtime.schemas import Market, MarketScanResult
from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)


class MarketScanner:
    """Scans and tracks active Polymarket markets.

    Features:
    - Full scan of all active markets with pagination
    - Category filtering
    - Automatic database persistence
    - Periodic scanning support
    - Safe deactivation with minimum threshold protection
    """

    # Minimum markets required before deactivation is allowed
    # Prevents false mass deactivation on partial/failed scans
    MIN_MARKETS_FOR_DEACTIVATION = 10

    def __init__(
        self,
        client: PolymarketClient,
        db: Database,
        page_size: int = 100,
    ) -> None:
        self._client = client
        self._db = db
        self._page_size = page_size
        self._is_running = False
        self._scan_task: Optional[asyncio.Task] = None
        self._scan_lock = asyncio.Lock()

    async def scan_all(
        self,
        category: Optional[str] = None,
        persist: bool = True,
    ) -> MarketScanResult:
        """Scan all active markets.

        Args:
            category: Optional category filter (e.g., "Politics").
            persist: Whether to save markets to database.

        Returns:
            MarketScanResult with all discovered markets and statistics.
        """
        async with self._scan_lock:
            logger.info(
                "Starting market scan",
                extra={"ctx_category": category or "all", "ctx_persist": persist},
            )

            all_markets: list[Market] = []
            seen_market_ids: set[str] = set()
            offset = 0
            total_pages = 0

            # Statistics
            new_count = 0
            updated_count = 0
            deactivated_count = 0
            failed_count = 0

            while True:
                markets = await self._client.list_markets(
                    active=True,
                    limit=self._page_size,
                    offset=offset,
                    category=category,
                )

                if not markets:
                    break

                total_pages += 1

                # Filter and validate markets
                for market in markets:
                    # Skip markets without valid ID
                    if not market.id:
                        failed_count += 1
                        logger.debug("Skipping market without id")
                        continue

                    # Skip duplicate markets
                    if market.id in seen_market_ids:
                        failed_count += 1
                        logger.debug(
                            "Skipping duplicate market",
                            extra={"ctx_market_id": market.id},
                        )
                        continue

                    # Filter out tokens with empty token_id
                    valid_tokens = [t for t in market.tokens if t.token_id]
                    if not valid_tokens:
                        failed_count += 1
                        logger.debug(
                            "Skipping market without valid tokens",
                            extra={"ctx_market_id": market.id},
                        )
                        continue

                    # Update tokens if some were filtered out
                    if len(valid_tokens) != len(market.tokens):
                        market = market.model_copy(update={"tokens": valid_tokens})

                    all_markets.append(market)
                    seen_market_ids.add(market.id)

                    # Persist to database
                    if persist:
                        try:
                            result = await self._db.upsert_market(market)
                            if result == "created":
                                new_count += 1
                            else:
                                updated_count += 1
                        except Exception as e:
                            failed_count += 1
                            logger.warning(
                                "Failed to persist market",
                                extra={"ctx_market_id": market.id, "ctx_error": str(e)},
                            )

                logger.debug(
                    "Scanned page",
                    extra={
                        "ctx_page": total_pages,
                        "ctx_markets_in_page": len(markets),
                        "ctx_total_so_far": len(all_markets),
                    },
                )

                if len(markets) < self._page_size:
                    break

                offset += self._page_size

            # Deactivate markets that no longer appear in scan
            # Only proceed if we have enough markets (safety check)
            scan_complete = len(all_markets) >= self.MIN_MARKETS_FOR_DEACTIVATION
            if persist and seen_market_ids and scan_complete:
                deactivated_count = await self._db.deactivate_missing_markets(
                    seen_market_ids,
                    category=category,
                )
            elif persist and not scan_complete and len(all_markets) > 0:
                logger.warning(
                    "Skipping deactivation due to insufficient markets",
                    extra={
                        "ctx_market_count": len(all_markets),
                        "ctx_threshold": self.MIN_MARKETS_FOR_DEACTIVATION,
                    },
                )

            # Always update metadata when persist=True (even on empty/partial scans)
            if persist:
                await self._db.set_metadata(
                    "last_scan_time", datetime.now(timezone.utc).isoformat()
                )
                await self._db.set_metadata("last_scan_count", str(len(all_markets)))
                await self._db.set_metadata("last_scan_new", str(new_count))
                await self._db.set_metadata("last_scan_updated", str(updated_count))
                await self._db.set_metadata("last_scan_deactivated", str(deactivated_count))
                await self._db.set_metadata(
                    "last_scan_complete", "true" if scan_complete else "false"
                )

            logger.info(
                "Market scan complete",
                extra={
                    "ctx_total_markets": len(all_markets),
                    "ctx_pages_scanned": total_pages,
                    "ctx_category": category or "all",
                    "ctx_new": new_count,
                    "ctx_updated": updated_count,
                    "ctx_deactivated": deactivated_count,
                    "ctx_failed": failed_count,
                    "ctx_scan_complete": scan_complete,
                },
            )

            return MarketScanResult(
                markets=all_markets,
                total_count=len(all_markets),
                new_count=new_count,
                updated_count=updated_count,
                deactivated_count=deactivated_count,
                failed_count=failed_count,
            )

    async def scan_categories(self, categories: list[str]) -> dict[str, MarketScanResult]:
        """Scan multiple categories.

        Args:
            categories: List of categories to scan.

        Returns:
            Dict mapping category to scan results.
        """
        results = {}
        for category in categories:
            results[category] = await self.scan_all(category=category)
        return results

    async def start_periodic_scan(
        self,
        interval_seconds: int,
        category: Optional[str] = None,
    ) -> None:
        """Start periodic background scanning.

        Args:
            interval_seconds: Seconds between scans.
            category: Optional category filter.
        """
        if self._is_running:
            logger.warning("Periodic scan already running")
            return

        self._is_running = True
        self._scan_task = asyncio.create_task(
            self._periodic_scan_loop(interval_seconds, category)
        )
        logger.info(
            "Started periodic scanning",
            extra={"ctx_interval": interval_seconds, "ctx_category": category or "all"},
        )

    async def _periodic_scan_loop(
        self,
        interval_seconds: int,
        category: Optional[str],
    ) -> None:
        """Internal loop for periodic scanning."""
        while self._is_running:
            try:
                await self.scan_all(category=category)
            except Exception as e:
                logger.error(
                    "Periodic scan failed",
                    extra={"ctx_error": str(e)},
                )

            await asyncio.sleep(interval_seconds)

    async def stop_periodic_scan(self) -> None:
        """Stop periodic scanning."""
        self._is_running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None
        logger.info("Stopped periodic scanning")

    async def get_last_scan_info(self) -> dict[str, Optional[str]]:
        """Get information about the last scan.

        Returns:
            Dict with last_scan_time and last_scan_count.
        """
        return {
            "last_scan_time": await self._db.get_metadata("last_scan_time"),
            "last_scan_count": await self._db.get_metadata("last_scan_count"),
        }

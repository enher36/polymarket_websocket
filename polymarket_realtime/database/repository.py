"""Database repository for Polymarket data persistence."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import aiosqlite

from polymarket_realtime.database.models import (
    CLEANUP_OLD_ORDERBOOK_SQL,
    CLEANUP_OLD_TRADES_SQL,
    SCHEMA_SQL,
)
from polymarket_realtime.schemas import Market, OrderbookSnapshot, Token, Trade
from polymarket_realtime.utils.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Async SQLite database repository.

    Manages a connection pool (single connection with lock for SQLite)
    and provides CRUD operations for Polymarket data.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._connection: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize database schema."""
        async with self._get_connection() as conn:
            await conn.executescript(SCHEMA_SQL)
            await conn.commit()
        logger.info("Database initialized", extra={"ctx_db_path": self.db_path})

    async def close(self) -> None:
        """Close database connection."""
        if self._connection:
            await self._connection.close()
            self._connection = None
            logger.info("Database connection closed")

    @asynccontextmanager
    async def _get_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Get database connection with lock."""
        async with self._lock:
            if self._connection is None:
                self._connection = await aiosqlite.connect(self.db_path)
                self._connection.row_factory = aiosqlite.Row
            yield self._connection

    # ==================== Market Operations ====================

    async def upsert_market(self, market: Market) -> str:
        """Insert or update a market and its tokens.

        Returns:
            "created" if new market inserted, "updated" if existing market updated.
        """
        async with self._get_connection() as conn:
            # Check if market already exists
            cursor = await conn.execute(
                "SELECT 1 FROM markets WHERE id = ?",
                (market.id,),
            )
            exists = await cursor.fetchone()

            now = datetime.now(timezone.utc).isoformat()
            await conn.execute(
                """
                INSERT INTO markets (id, slug, question, category, active, end_date, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    question = excluded.question,
                    category = excluded.category,
                    active = excluded.active,
                    end_date = excluded.end_date,
                    updated_at = excluded.updated_at
                """,
                (
                    market.id,
                    market.slug,
                    market.question,
                    market.category,
                    1 if market.active else 0,
                    market.end_date.isoformat() if market.end_date else None,
                    now,
                ),
            )

            # Upsert tokens (skip empty token_id)
            for token in market.tokens:
                if not token.token_id:
                    continue
                await self._upsert_token(conn, market.id, token)

            await conn.commit()
            return "updated" if exists else "created"

    async def _upsert_token(
        self, conn: aiosqlite.Connection, market_id: str, token: Token
    ) -> None:
        """Insert or update a token."""
        await conn.execute(
            """
            INSERT INTO tokens (token_id, market_id, outcome, symbol)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(token_id) DO UPDATE SET
                outcome = excluded.outcome,
                symbol = excluded.symbol
            """,
            (token.token_id, market_id, token.outcome, token.symbol),
        )

    async def get_market_by_slug(self, slug: str) -> Optional[dict]:
        """Get market by slug."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM markets WHERE slug = ?", (slug,)
            )
            row = await cursor.fetchone()
            if row:
                return dict(row)
            return None

    async def get_token_ids_by_market(self, market_id: str) -> list[tuple[str, str]]:
        """Get token IDs for a market.

        Returns:
            List of (token_id, outcome) tuples.
        """
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT token_id, outcome FROM tokens WHERE market_id = ?",
                (market_id,),
            )
            rows = await cursor.fetchall()
            return [(row["token_id"], row["outcome"]) for row in rows]

    async def list_active_markets(
        self, category: Optional[str] = None, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        """List active markets with optional category filter."""
        async with self._get_connection() as conn:
            if category:
                cursor = await conn.execute(
                    """
                    SELECT * FROM markets
                    WHERE active = 1 AND category = ?
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (category, limit, offset),
                )
            else:
                cursor = await conn.execute(
                    """
                    SELECT * FROM markets
                    WHERE active = 1
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def list_active_market_ids(self, category: Optional[str] = None) -> set[str]:
        """Get set of all active market IDs.

        Args:
            category: Optional category filter.

        Returns:
            Set of market IDs.
        """
        async with self._get_connection() as conn:
            if category:
                cursor = await conn.execute(
                    "SELECT id FROM markets WHERE active = 1 AND category = ?",
                    (category,),
                )
            else:
                cursor = await conn.execute(
                    "SELECT id FROM markets WHERE active = 1",
                )
            rows = await cursor.fetchall()
            return {row["id"] for row in rows}

    async def deactivate_missing_markets(
        self,
        active_market_ids: set[str],
        category: Optional[str] = None,
    ) -> int:
        """Mark markets not in active_market_ids as inactive.

        Args:
            active_market_ids: Set of market IDs observed in latest scan.
            category: Optional category filter to scope the update.

        Returns:
            Number of markets deactivated.
        """
        if not active_market_ids:
            return 0

        async with self._get_connection() as conn:
            now = datetime.now(timezone.utc).isoformat()
            placeholders = ",".join("?" for _ in active_market_ids)

            if category:
                cursor = await conn.execute(
                    f"""
                    UPDATE markets
                    SET active = 0, updated_at = ?
                    WHERE active = 1
                      AND category = ?
                      AND id NOT IN ({placeholders})
                    """,
                    (now, category, *active_market_ids),
                )
            else:
                cursor = await conn.execute(
                    f"""
                    UPDATE markets
                    SET active = 0, updated_at = ?
                    WHERE active = 1
                      AND id NOT IN ({placeholders})
                    """,
                    (now, *active_market_ids),
                )

            await conn.commit()
            return cursor.rowcount

    # ==================== Trade Operations ====================

    async def save_trade(self, trade: Trade) -> bool:
        """Save a trade (idempotent via trade_id).

        Returns:
            True if inserted, False if already exists.
        """
        async with self._get_connection() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO trades (trade_id, token_id, price, amount, taker_side, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.trade_id,
                        trade.token_id,
                        str(trade.price),
                        str(trade.amount),
                        trade.taker_side,
                        trade.timestamp.isoformat(),
                    ),
                )
                await conn.commit()
                return True
            except aiosqlite.IntegrityError:
                # Trade already exists
                return False

    async def get_recent_trades(
        self, token_id: str, limit: int = 100
    ) -> list[dict]:
        """Get recent trades for a token."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM trades
                WHERE token_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (token_id, limit),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    # ==================== Orderbook Operations ====================

    async def upsert_orderbook(self, snapshot: OrderbookSnapshot) -> None:
        """Update orderbook levels from snapshot."""
        async with self._get_connection() as conn:
            received_at = snapshot.received_at.isoformat()

            # Upsert bids
            for level in snapshot.bids:
                await conn.execute(
                    """
                    INSERT INTO orderbook_levels (token_id, side, price, size, sequence, received_at)
                    VALUES (?, 'bid', ?, ?, ?, ?)
                    ON CONFLICT(token_id, side, price) DO UPDATE SET
                        size = excluded.size,
                        sequence = excluded.sequence,
                        received_at = excluded.received_at
                    """,
                    (
                        snapshot.token_id,
                        str(level.price),
                        str(level.size),
                        snapshot.sequence,
                        received_at,
                    ),
                )

            # Upsert asks
            for level in snapshot.asks:
                await conn.execute(
                    """
                    INSERT INTO orderbook_levels (token_id, side, price, size, sequence, received_at)
                    VALUES (?, 'ask', ?, ?, ?, ?)
                    ON CONFLICT(token_id, side, price) DO UPDATE SET
                        size = excluded.size,
                        sequence = excluded.sequence,
                        received_at = excluded.received_at
                    """,
                    (
                        snapshot.token_id,
                        str(level.price),
                        str(level.size),
                        snapshot.sequence,
                        received_at,
                    ),
                )

            # Remove zero-size levels
            await conn.execute(
                "DELETE FROM orderbook_levels WHERE token_id = ? AND size = '0'",
                (snapshot.token_id,),
            )

            await conn.commit()

    async def get_orderbook(self, token_id: str) -> dict:
        """Get current orderbook for a token."""
        async with self._get_connection() as conn:
            bids_cursor = await conn.execute(
                """
                SELECT price, size FROM orderbook_levels
                WHERE token_id = ? AND side = 'bid'
                ORDER BY CAST(price AS REAL) DESC
                """,
                (token_id,),
            )
            asks_cursor = await conn.execute(
                """
                SELECT price, size FROM orderbook_levels
                WHERE token_id = ? AND side = 'ask'
                ORDER BY CAST(price AS REAL) ASC
                """,
                (token_id,),
            )

            bids = await bids_cursor.fetchall()
            asks = await asks_cursor.fetchall()

            return {
                "token_id": token_id,
                "bids": [[row["price"], row["size"]] for row in bids],
                "asks": [[row["price"], row["size"]] for row in asks],
            }

    # ==================== Cleanup Operations ====================

    async def cleanup_old_trades(self, days: int = 7) -> int:
        """Remove trades older than specified days.

        Returns:
            Number of deleted rows.
        """
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                CLEANUP_OLD_TRADES_SQL, (f"-{days} days",)
            )
            await conn.commit()
            return cursor.rowcount

    async def cleanup_old_orderbook(self, hours: int = 24) -> int:
        """Remove orderbook snapshots older than specified hours.

        Returns:
            Number of deleted rows.
        """
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                CLEANUP_OLD_ORDERBOOK_SQL, (f"-{hours} hours",)
            )
            await conn.commit()
            return cursor.rowcount

    # ==================== Metadata Operations ====================

    async def get_metadata(self, key: str) -> Optional[str]:
        """Get metadata value by key."""
        async with self._get_connection() as conn:
            cursor = await conn.execute(
                "SELECT value FROM metadata WHERE key = ?", (key,)
            )
            row = await cursor.fetchone()
            return row["value"] if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        """Set metadata value."""
        async with self._get_connection() as conn:
            await conn.execute(
                """
                INSERT INTO metadata (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value),
            )
            await conn.commit()

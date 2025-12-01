"""SQLite database schema definitions."""

SCHEMA_SQL = """
-- Markets table
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    question TEXT NOT NULL,
    category TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    end_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active);

-- Tokens table
CREATE TABLE IF NOT EXISTS tokens (
    token_id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    symbol TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (market_id) REFERENCES markets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tokens_market ON tokens(market_id);
CREATE INDEX IF NOT EXISTS idx_tokens_outcome ON tokens(outcome);

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    token_id TEXT NOT NULL,
    price TEXT NOT NULL,
    amount TEXT NOT NULL,
    taker_side TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (token_id) REFERENCES tokens(token_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token_id);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_trades_token_time ON trades(token_id, timestamp DESC);

-- Orderbook snapshots table
CREATE TABLE IF NOT EXISTS orderbook_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('bid', 'ask')),
    price TEXT NOT NULL,
    size TEXT NOT NULL,
    sequence INTEGER,
    received_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (token_id, side, price),
    FOREIGN KEY (token_id) REFERENCES tokens(token_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_orderbook_token ON orderbook_levels(token_id);
CREATE INDEX IF NOT EXISTS idx_orderbook_side ON orderbook_levels(token_id, side);

-- Metadata table for tracking scan state
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# SQL for cleanup operations
CLEANUP_OLD_TRADES_SQL = """
DELETE FROM trades
WHERE timestamp < datetime('now', ?)
"""

CLEANUP_OLD_ORDERBOOK_SQL = """
DELETE FROM orderbook_levels
WHERE received_at < datetime('now', ?)
"""

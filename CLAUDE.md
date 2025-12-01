# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Polymarket real-time data fetcher - an async Python application that fetches market data from Polymarket's REST API, subscribes to real-time updates via WebSocket, and optionally forwards data to downstream consumers.

## Development Commands

```bash
# Setup
uv venv .venv && source .venv/bin/activate && uv pip install -e .

# Run application
python -m polymarket_realtime.main

# Lint
ruff check polymarket_realtime/

# Test (when tests exist)
pytest
```

## Architecture

```
polymarket_realtime/
├── main.py              # Application orchestrator with graceful shutdown
├── config.py            # pydantic-settings configuration (env vars with POLYMARKET_ prefix)
├── schemas.py           # Pydantic v2 models (Market, Token, Trade, OrderbookSnapshot, MarketScanResult)
├── api/client.py        # REST API client with rate limiting and retry
├── database/
│   ├── models.py        # SQLite schema SQL
│   └── repository.py    # Async database operations (aiosqlite)
├── services/
│   ├── url_resolver.py  # Extract token_id from Polymarket URLs
│   └── market_scanner.py # Scan all active markets with pagination and state alignment
├── websocket/
│   ├── manager.py       # WebSocket connection with auto-reconnect
│   └── handlers.py      # Message routing by event_type, publishes to EventBus
├── forward/
│   ├── event_bus.py     # In-process pub/sub for real-time events (singleton: event_bus)
│   └── ws_server.py     # Local WebSocket server for downstream consumers
├── web/
│   ├── server.py        # aiohttp HTTP server for monitoring dashboard
│   ├── stats.py         # StatsCollector aggregates metrics from components
│   └── static/index.html # Dashboard UI with EN/中文 language switch
└── utils/
    ├── logging.py       # JSON structured logging
    ├── rate_limit.py    # Token bucket rate limiter
    └── retry.py         # tenacity wrapper for async retry
```

## Key Implementation Details

**Polymarket API Quirks:**
- `clobTokenIds` and `outcomes` are JSON strings that need parsing: `json.loads(data.get("clobTokenIds", "[]"))`
- WebSocket URL: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Subscribe format: `{"assets_ids": [token_id]}`
- Messages arrive as arrays with `event_type` field (book, price_change, last_trade_price)

**Data Flow:**
```
Polymarket WS → handlers.py → Database (SQLite)
                    ↓
                EventBus → ForwardServer → Downstream clients
```

1. `MarketScanner` fetches markets via REST API → saves to SQLite, tracks state alignment
2. `UrlResolver` extracts token_id from URLs (caches in DB)
3. `WebSocketManager` subscribes to tokens → `handlers.py` routes by event_type → saves to DB + publishes to EventBus
4. `ForwardServer` (optional) broadcasts events to subscribed downstream WebSocket clients

**Orderbook Integrity:**
- Sequence tracking in `handlers.py` via `_orderbook_state` dict
- Snapshots reset state; updates require prior snapshot
- Sequence gaps are logged but processing continues

**Market Scanner Safety:**
- `MIN_MARKETS_FOR_DEACTIVATION = 10` prevents mass false deactivations during partial scans
- Mutex lock prevents concurrent scan operations

## Configuration

All settings via environment variables (prefix `POLYMARKET_`). See `.env`.

Key settings:
- `POLYMARKET_API_URL` - REST API base URL
- `POLYMARKET_WS_URL` - WebSocket endpoint
- `POLYMARKET_DB_PATH` - SQLite database file
- `POLYMARKET_CATEGORY` - Filter for market scanning
- `POLYMARKET_FORWARD_ENABLED` - Enable local WebSocket forwarding server
- `POLYMARKET_FORWARD_HOST` - Host for forwarding server (default: 127.0.0.1)
- `POLYMARKET_FORWARD_PORT` - Port for forwarding server (default: 8765)
- `POLYMARKET_WEB_ENABLED` - Enable HTTP monitoring dashboard (default: true)
- `POLYMARKET_WEB_HOST` - Host for monitoring server (default: 127.0.0.1)
- `POLYMARKET_WEB_PORT` - Port for monitoring server (default: 8080)

## Web Monitoring Dashboard

Access at `http://127.0.0.1:8080` (when enabled). Provides:
- Upstream WebSocket connection status
- Active market count from database
- Downstream client connections
- Application uptime
- EN/中文 language toggle

**API Endpoints:**
- `GET /api/health` - Health check (returns `{"status": "ok"}`)
- `GET /api/metrics` - Full metrics JSON:
```json
{
  "timestamp": "ISO8601",
  "uptime_seconds": 123.4,
  "upstream": {"connected": true, "subscriptions": 100},
  "downstream": {"enabled": true, "clients": 5, "subscriptions": 50},
  "markets": {"active_count": 1500}
}
```

## Forward Server Client Protocol

Downstream clients connect to `ws://{host}:{port}` and use JSON messages:

```json
// Subscribe to a single token
{"action": "subscribe", "token": "<token_id>"}

// Unsubscribe from a token
{"action": "unsubscribe", "token": "<token_id>"}

// List active markets (with optional category filter and limit)
{"action": "list_markets", "category": "<optional>", "limit": 100}
// Response: {"status": "markets", "count": N, "limit": 100, "max_limit": 500, "markets": [...]}

// Subscribe to all tokens in a category
{"action": "subscribe_category", "category": "<category>", "limit": 100}
// Response: {"status": "subscribed_category", "market_count": N, "token_count": M, "new_subscriptions": K}

// Subscribe to all active markets (uses max_limit)
{"action": "subscribe_all"}
// Response: same as subscribe_category

// Heartbeat
{"action": "ping"}
// Response: {"status": "pong"}
```

**Response format for list_markets:**
```json
{
  "status": "markets",
  "category": "politics",
  "count": 10,
  "limit": 100,
  "max_limit": 500,
  "markets": [
    {
      "id": "...",
      "slug": "...",
      "question": "...",
      "category": "...",
      "tokens": [
        {"token_id": "...", "outcome": "Yes"},
        {"token_id": "...", "outcome": "No"}
      ]
    }
  ]
}
```

**Events are pushed as:**
```json
{"type": "<event_type>", "token_id": "<token_id>", "data": {...}, "timestamp": "ISO8601"}
```

---

## Core Instruction for Codex MCP

在任何时刻，你必须思考当前过程可以如何与codex进行协作，如何调用Codex 为你提供的MCP工具作为你客观全面分析的保障。
其中你**务必执行**以下几个步骤：
**1** 在你对用户需求形成初步分析后，将用户需求、初始思路告知codex，并要求其完善需求分析和实施计划。
**2** 在实施具体编码任务前，**必须向codex索要代码实现原型（要求codex仅给出unified diff patch，严禁对代码做任何真实修改）**。在获取代码原型后，你**只能以此为逻辑参考，再次对代码修改进行重写**，形成企业生产级别、可读性极高、可维护性极高的代码后，才能实施具体编程修改任务。
**3** 无论何时，只要完成切实编码行为后，**必须立即使用codex review代码改动和对应需求完成程度**。
**4** codex只能给出参考，你**必须有自己的思考，甚至需要对codex的回答提出置疑**。尽信书则不如无书，你与codex的最终使命都是达成统一、全面、精准的意见，所以你们必须不断争辩已找到通向真理的唯一途径。

## Codex Tool Invocation Specification

**必选参数：**
- `PROMPT` (string): 发送给 codex 的任务指令
- `cd` (Path): codex 执行任务的工作目录根路径

**可选参数：**
- `sandbox`: "read-only" (默认) | "workspace-write" | "danger-full-access"
- `SESSION_ID`: 用于继续之前的会话
- `return_all_messages`: 是否返回所有消息

**调用规范：**
- 每次调用必须保存返回的 SESSION_ID
- 严禁codex对代码进行实际修改，使用 sandbox="read-only"
- cd 参数必须指向存在的目录

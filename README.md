# Polymarket Real-time Data Fetcher

异步 Python 应用程序，用于从 Polymarket 获取市场数据、订阅实时更新并转发给下游消费者。

## 功能特性

- **REST API 客户端**: 带速率限制和自动重试
- **WebSocket 管理器**: 自动重连和心跳保活
- **市场扫描器**: 分页扫描、状态对齐、定时更新
- **SQLite 数据库**: 异步操作，本地持久化
- **事件总线**: 进程内发布-订阅模式
- **转发服务器**: 本地 WebSocket 服务器供下游消费者使用

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (推荐) 或 pip

### 安装

```bash
# 克隆仓库
git clone https://github.com/enher36/polymarket_websocket.git
cd polymarket_websocket

# 使用 uv 创建虚拟环境并安装依赖
uv venv .venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

uv pip install -e .
```

### 配置

复制环境变量示例文件并按需修改：

```bash
cp .env.example .env
```

主要配置项（环境变量前缀 `POLYMARKET_`）：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `POLYMARKET_API_URL` | `https://gamma-api.polymarket.com` | REST API 地址 |
| `POLYMARKET_WS_URL` | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | WebSocket 地址 |
| `POLYMARKET_DB_PATH` | `polymarket.db` | SQLite 数据库路径 |
| `POLYMARKET_CATEGORY` | `all` | 市场扫描分类过滤 |
| `POLYMARKET_FORWARD_ENABLED` | `false` | 启用转发服务器 |
| `POLYMARKET_FORWARD_HOST` | `127.0.0.1` | 转发服务器主机 |
| `POLYMARKET_FORWARD_PORT` | `8765` | 转发服务器端口 |
| `POLYMARKET_LOG_LEVEL` | `INFO` | 日志级别 |
| `POLYMARKET_SCAN_INTERVAL_SEC` | `300` | 市场扫描间隔（秒） |

### 运行

```bash
# 激活虚拟环境
source .venv/bin/activate

# 运行应用
python -m polymarket_realtime.main

# 或使用安装的命令
polymarket
```

### 运行示例输出

```json
{"timestamp": "2025-12-01T06:00:41Z", "level": "INFO", "message": "Starting Polymarket real-time fetcher"}
{"timestamp": "2025-12-01T06:00:41Z", "level": "INFO", "message": "Database initialized"}
{"timestamp": "2025-12-01T06:00:41Z", "level": "INFO", "message": "Running initial market scan"}
{"timestamp": "2025-12-01T06:00:45Z", "level": "INFO", "message": "Initial scan complete", "markets": 1500}
```

## 转发服务器使用

启用转发服务器后，下游客户端可以通过 WebSocket 连接获取实时数据。

### 连接

```javascript
const ws = new WebSocket('ws://127.0.0.1:8765');
```

### 客户端协议

```json
// 订阅 token
{"action": "subscribe", "token": "<token_id>"}

// 取消订阅
{"action": "unsubscribe", "token": "<token_id>"}

// 心跳
{"action": "ping"}
```

### 接收事件

```json
{
  "type": "book",
  "token_id": "<token_id>",
  "data": {"bids": [...], "asks": [...]},
  "timestamp": "2025-12-01T06:00:41Z"
}
```

## 开发

### 代码检查

```bash
# 安装开发依赖
uv pip install -e ".[dev]"

# 运行 lint
ruff check polymarket_realtime/
```

### 项目结构

```
polymarket_realtime/
├── main.py              # 应用入口和编排
├── config.py            # 配置管理
├── schemas.py           # 数据模型
├── api/
│   └── client.py        # REST API 客户端
├── database/
│   ├── models.py        # 数据库 Schema
│   └── repository.py    # 数据库操作
├── services/
│   ├── market_scanner.py # 市场扫描器
│   └── url_resolver.py   # URL 解析器
├── websocket/
│   ├── manager.py       # WebSocket 连接管理
│   └── handlers.py      # 消息处理和路由
├── forward/
│   ├── event_bus.py     # 事件总线
│   └── ws_server.py     # 转发服务器
└── utils/
    ├── logging.py       # 日志工具
    ├── rate_limit.py    # 速率限制
    └── retry.py         # 重试逻辑
```

## 数据流

```
Polymarket REST API ──→ MarketScanner ──→ SQLite DB
                                              ↓
Polymarket WebSocket ──→ handlers.py ──→ SQLite DB
                              ↓
                          EventBus ──→ ForwardServer ──→ 下游客户端
```

## 许可证

MIT License

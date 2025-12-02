# 上游转发服务器 API 需求文档

## 问题描述

Web Client 连接上游转发服务器后无法显示任何实时数据。

## 根本原因

经测试，当前上游服务器 (`ws://92.113.142.51:8765`) 只支持两个 action：

| Action | 功能 | 响应示例 |
|--------|------|----------|
| `ping` | 心跳检测 | `{"status": "pong"}` |
| `list_markets` | 获取市场列表 | `{"status": "markets", "markets": [...]}` |

**问题**: 上游服务器是**请求-响应**模式，不支持**订阅-推送**模式。

Web Client 后端期望上游服务器支持：
- 订阅特定 token 的实时数据
- 主动推送 orderbook、trade、price_change 事件

## 需要添加的 API

### 1. 订阅 (subscribe)

**请求**:
```json
{
  "action": "subscribe",
  "token_id": "114563309536528749390488662475257394441921087931650562623361207584160724689794"
}
```

**响应**:
```json
{
  "type": "subscribed",
  "token_id": "114563309536528749390488662475257394441921087931650562623361207584160724689794"
}
```

### 2. 批量订阅 (subscribe_batch)

**请求**:
```json
{
  "action": "subscribe_batch",
  "token_ids": ["token_id_1", "token_id_2", "token_id_3"]
}
```

**响应**:
```json
{
  "type": "subscribed_batch",
  "token_ids": ["token_id_1", "token_id_2", "token_id_3"]
}
```

### 3. 取消订阅 (unsubscribe)

**请求**:
```json
{
  "action": "unsubscribe",
  "token_id": "..."
}
```

**响应**:
```json
{
  "type": "unsubscribed",
  "token_id": "..."
}
```

## 需要推送的事件

订阅后，上游服务器应主动推送以下事件：

### 1. Orderbook 更新

```json
{
  "type": "orderbook",
  "token_id": "...",
  "data": {
    "bids": [{"price": 0.55, "size": 100.0}, ...],
    "asks": [{"price": 0.56, "size": 150.0}, ...],
    "sequence": 12345,
    "timestamp": "2025-12-03T12:00:00Z"
  }
}
```

### 2. Trade 成交

```json
{
  "type": "trade",
  "token_id": "...",
  "data": {
    "id": "trade_123",
    "price": 0.55,
    "size": 50.0,
    "side": "buy",
    "timestamp": "2025-12-03T12:00:00Z"
  }
}
```

### 3. Price Change 价格变动

```json
{
  "type": "price_change",
  "token_id": "...",
  "data": {
    "price": 0.55,
    "change_24h": 0.02,
    "timestamp": "2025-12-03T12:00:00Z"
  }
}
```

## 实现建议

### 方案 A: 订阅管理器模式

```
客户端连接 → 发送 subscribe → 服务器记录订阅
                                    ↓
                              Polymarket API 推送数据
                                    ↓
                              过滤已订阅的 token
                                    ↓
                              推送给对应客户端
```

伪代码:
```python
# 服务器端
subscriptions = {}  # client_id -> set(token_ids)

async def handle_subscribe(client, token_id):
    subscriptions.setdefault(client.id, set()).add(token_id)
    await client.send({"type": "subscribed", "token_id": token_id})

async def on_polymarket_event(event):
    token_id = event["token_id"]
    for client_id, tokens in subscriptions.items():
        if token_id in tokens:
            await clients[client_id].send(event)
```

### 方案 B: 频道广播模式

```
token_id_1 频道 ← 客户端A, 客户端B
token_id_2 频道 ← 客户端B, 客户端C
```

每个 token_id 作为一个频道，订阅者加入频道，事件广播给频道内所有订阅者。

## 测试验证

修改完成后，可用以下脚本测试：

```python
import asyncio
import websockets
import json

async def test_subscribe():
    uri = "ws://YOUR_SERVER:8765"
    async with websockets.connect(uri) as ws:
        # 测试订阅
        await ws.send(json.dumps({
            "action": "subscribe",
            "token_id": "YOUR_TOKEN_ID"
        }))

        # 等待订阅确认
        response = await ws.recv()
        print(f"Subscribe response: {response}")

        # 等待推送事件
        print("Waiting for events...")
        while True:
            event = await ws.recv()
            print(f"Event: {event}")

asyncio.run(test_subscribe())
```

## 当前支持的 API (参考)

| Action | 参数 | 说明 |
|--------|------|------|
| `ping` | 无 | 心跳检测 |
| `list_markets` | `limit`, `category` | 获取市场列表 |

---

**创建日期**: 2025-12-03
**状态**: 待实现

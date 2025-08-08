"""
FastAPI application for the Mr. Teals trading bot.

This backend provides a simple skeleton for a crypto trading bot with
WebSocket price streaming, REST endpoints for controlling the bot and
retrieving state, and in-memory data stores for demonstration.
"""

import asyncio
import datetime
import random
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Mr. Teals Bot API")

# Allow all origins for demo; restrict in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
watchlist: List[str] = ["BTC/USD", "ETH/USD", "SOL/USD"]
bot_running = False
bot_paused = False
bot_task: Optional[asyncio.Task] = None

account_snapshot: Dict[str, float] = {
    "equity": 10000.0,
    "cash": 10000.0,
    "positions_value": 0.0,
    "unrealized_pnl": 0.0,
}

performance_today: Dict[str, float] = {
    "realized_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "trades_count": 0,
}

last_trade: Dict[str, Optional[str]] = {
    "symbol": None,
    "side": None,
    "quantity": None,
    "price": None,
    "time": None,
    "strategy": None,
}

settings: Dict[str, float] = {
    "position_size": 1000.0,
    "max_daily_loss": 1000.0,
    "stop_loss_pct": 0.05,
}
async def price_feed():
    global bot_running, bot_paused
    try:
        while bot_running:
            if bot_paused:
                await asyncio.sleep(1)
                continue
            timestamp = datetime.datetime.utcnow().isoformat()
            price_data: Dict[str, float] = {}
            for symbol in watchlist:
                price_data[symbol] = random.uniform(100, 40000)
            await manager.broadcast({
                "type": "prices",
                "timestamp": timestamp,
                "data": price_data,
            })
            await asyncio.sleep(1)
        await manager.broadcast({"type": "status", "status": "stopped"})
    except asyncio.CancelledError:
        pass

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
@app.post("/api/control/start")
async def start_bot() -> Dict[str, str]:
    global bot_running, bot_paused, bot_task
    if not bot_running:
        bot_running = True
        bot_paused = False
        bot_task = asyncio.create_task(price_feed())
    return {"status": "running"}

@app.post("/api/control/pause")
async def pause_bot() -> Dict[str, str]:
    global bot_running, bot_paused
    if bot_running:
        bot_paused = True
        return {"status": "paused"}
    return {"status": "stopped"}

@app.post("/api/control/kill")
async def kill_bot() -> Dict[str, str]:
    global bot_running, bot_paused, bot_task
    bot_running = False
    bot_paused = False
    if bot_task:
        bot_task.cancel()
        bot_task = None
    return {"status": "killed"}
@app.get("/api/watchlist")
async def get_watchlist() -> Dict[str, List[str]]:
    return {"watchlist": watchlist}

@app.post("/api/watchlist/add")
async def add_symbol(symbol: str = Query(..., description="Symbol to add e.g. BTC/USD")) -> Dict[str, List[str]]:
    sym = symbol.upper()
    if sym not in watchlist:
        watchlist.append(sym)
    return {"watchlist": watchlist}

@app.post("/api/watchlist/remove")
async def remove_symbol(symbol: str = Query(..., description="Symbol to remove")) -> Dict[str, List[str]]:
    sym = symbol.upper()
    if sym in watchlist:
        watchlist.remove(sym)
    return {"watchlist": watchlist}
@app.get("/api/account/snapshot")
async def get_account_snapshot() -> Dict[str, float]:
    return account_snapshot

@app.get("/api/performance/today")
async def get_performance_today() -> Dict[str, float]:
    return performance_today

@app.get("/api/trades/last")
async def get_last_trade() -> Dict[str, Optional[str]]:
    return last_trade

@app.post("/api/settings/risk")
async def update_risk(
    position_size: Optional[float] = Query(None),
    max_daily_loss: Optional[float] = Query(None),
    stop_loss_pct: Optional[float] = Query(None)
) -> Dict[str, float]:
    if position_size is not None:
        settings["position_size"] = position_size
    if max_daily_loss is not None:
        settings["max_daily_loss"] = max_daily_loss
    if stop_loss_pct is not None:
        settings["stop_loss_pct"] = stop_loss_pct
    return settings

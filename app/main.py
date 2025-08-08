"""
FastAPI application for the Mr. Teals trading bot.

This backend provides a simple skeleton for a crypto trading bot with
WebSocket price streaming, REST endpoints for controlling the bot and
retrieving state, and in‑memory data stores for demonstration. The goal of
this file is to give you a working starting point that can be extended
with real strategy logic, exchange connectivity, and database persistence.

Features implemented here include:
* Start, pause and kill controls for the bot engine.
* A WebSocket endpoint that pushes simulated price data to connected clients.
* Endpoints for managing a watchlist of symbols.
* Endpoints for retrieving a snapshot of account state, performance metrics
  for today, and the last executed trade.
* A simple risk settings store that can be updated via an endpoint.

This code is intended for educational and paper‑trading purposes only. It
does not execute real trades or connect to any exchange by default. If you
decide to extend it for live trading, ensure you thoroughly test your
strategies and comply with all regulatory requirements. Executing live
trades carries significant financial risk, and you should never deploy
untrusted code against real funds.
"""

import asyncio
import datetime
import json
import random
import requests
from typing import Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Mr. Teals Bot API")

# Allow all origins for demo purposes. When deploying to production you
# should set this to your frontend's domain to improve security.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    """Manage WebSocket connections and broadcast messages to clients."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            # Not in list, ignore
            pass

    async def broadcast(self, message: Dict) -> None:
        """Send a JSON‑serialisable message to all connected clients."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                # If sending fails, drop the connection
                self.disconnect(connection)


# Instantiate a global connection manager
manager = ConnectionManager()

# In‑memory stores for demonstration. In a production system these values
# should live in a database so they persist across restarts and scale across
# multiple instances.
# Initialise the watchlist with a handful of common crypto pairs. These symbols
# will appear by default on the front‑end and can be updated via the
# watchlist add/remove endpoints. Keeping a larger default list makes the
# dashboard feel populated on first load. Feel free to add more symbols here
# as long as you also provide a mapping in SYMBOL_TO_ID below.
watchlist: List[str] = ["BTC/USD", "ETH/USD", "SOL/USD", "DOT/USD", "DOGE/USD"]
bot_running: bool = False
bot_paused: bool = False
bot_task: Optional[asyncio.Task] = None

# Simple account state for demonstration. Values are floats for ease of use;
# you may wish to use Decimal in a real system to avoid floating point
# precision issues.
account_snapshot: Dict[str, float] = {
    "equity": 10_000.0,
    "cash": 10_000.0,
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

# Risk settings with sensible defaults. Position size is the maximum
# allocation per trade; max_daily_loss sets a daily circuit breaker; and
# stop_loss_pct defines a trailing stop.
settings: Dict[str, float] = {
    "position_size": 1_000.0,
    "max_daily_loss": 1_000.0,
    "stop_loss_pct": 0.05,
}

# --- Additional state for manual trading and realistic pricing ---
# positions tracks the quantity of each asset currently held in the paper account.
positions: Dict[str, float] = {}

# trade_log records executed trades (manual or automated) with timestamp, side, symbol, quantity and price.
trade_log: List[Dict[str, str]] = []

# initial_balance defines the starting cash balance when the account is reset. You can adjust this via the reset endpoint.
initial_balance: float = 10_000.0

# Map our symbol format to CoinGecko IDs. This is required to fetch live prices. Add new mappings here as you
# add symbols to your watchlist. See https://api.coingecko.com/api/v3/coins/list for valid IDs.
SYMBOL_TO_ID: Dict[str, str] = {
    "BTC/USD": "bitcoin",
    "ETH/USD": "ethereum",
    "SOL/USD": "solana",
    "DOT/USD": "polkadot",
    "DOGE/USD": "dogecoin",
    "ADA/USD": "cardano",
}

def fetch_live_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch current USD prices for the given symbols using the CoinGecko API.

    Returns a mapping of symbol -> price. If the API call fails or a symbol is
    unknown, that entry will be absent from the result. This function uses
    synchronous HTTP requests; in a production system you may wish to cache
    results or use an async client.
    """
    ids = [SYMBOL_TO_ID[sym] for sym in symbols if sym in SYMBOL_TO_ID]
    if not ids:
        return {}
    url = (
        "https://api.coingecko.com/api/v3/simple/price?ids="
        + ",".join(ids)
        + "&vs_currencies=usd"
    )
    try:
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return {}
    prices: Dict[str, float] = {}
    for sym in symbols:
        coingecko_id = SYMBOL_TO_ID.get(sym)
        if coingecko_id and coingecko_id in data:
            try:
                prices[sym] = float(data[coingecko_id]["usd"])
            except (KeyError, TypeError, ValueError):
                continue
    return prices

def update_account_snapshot() -> None:
    """Recalculate the account snapshot based on current positions and live prices."""
    global account_snapshot
    # Fetch prices for held positions
    current_prices = fetch_live_prices(list(positions.keys())) if positions else {}
    positions_value = 0.0
    unrealized_pnl = 0.0
    for sym, qty in positions.items():
        price = current_prices.get(sym)
        if price is not None:
            positions_value += qty * price
    account_snapshot["positions_value"] = positions_value
    account_snapshot["unrealized_pnl"] = unrealized_pnl  # placeholder for future cost basis calculations
    account_snapshot["equity"] = account_snapshot["cash"] + positions_value


async def price_feed() -> None:
    """
    Simulate a price feed and broadcast updates via WebSocket.

    This coroutine runs in an asynchronous loop while the bot is marked as
    running. It generates random price data for each symbol in the watchlist
    and broadcasts it to all connected clients. If the bot is paused, the
    coroutine sleeps briefly without sending updates. When the bot stops
    running, a "stopped" status message is broadcast to clients.
    """
    global bot_running, bot_paused
    try:
        while bot_running:
            if bot_paused:
                await asyncio.sleep(1)
                continue
            timestamp = datetime.datetime.utcnow().isoformat()
            price_data: Dict[str, float] = {}
            for symbol in watchlist:
                # Generate a pseudo‑random price within a broad range. In a real
                # system you would fetch live market data here.
                price = random.uniform(100, 40_000)
                price_data[symbol] = price
            payload = {
                "type": "prices",
                "timestamp": timestamp,
                "data": price_data,
            }
            await manager.broadcast(payload)
            await asyncio.sleep(1)
        # If the loop exits, notify clients that the feed stopped
        await manager.broadcast({"type": "status", "status": "stopped"})
    except asyncio.CancelledError:
        # Task cancelled, propagate the event
        pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Handle WebSocket connections from clients."""
    await manager.connect(websocket)
    try:
        while True:
            # The server does not expect to receive messages from clients,
            # but we keep the receive loop alive to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/control/start")
async def start_bot() -> Dict[str, str]:
    """Start the price feed loop."""
    global bot_running, bot_paused, bot_task
    if not bot_running:
        bot_running = True
        bot_paused = False
        bot_task = asyncio.create_task(price_feed())
    return {"status": "running"}


@app.post("/api/control/pause")
async def pause_bot() -> Dict[str, str]:
    """Pause the price feed without stopping it completely."""
    global bot_running, bot_paused
    if bot_running:
        bot_paused = True
        return {"status": "paused"}
    return {"status": "stopped"}


@app.post("/api/control/kill")
async def kill_bot() -> Dict[str, str]:
    """Stop the price feed loop entirely."""
    global bot_running, bot_paused, bot_task
    bot_running = False
    bot_paused = False
    if bot_task is not None:
        # Cancel the running price feed coroutine. The cancellation will
        bot_task.cancel()
        bot_task = None
    return {"status": "killed"}


@app.get("/api/watchlist")
async def get_watchlist() -> Dict[str, List[str]]:
    """Return the current watchlist."""
    return {"watchlist": watchlist}


@app.post("/api/watchlist/add")
async def add_symbol(symbol: str = Query(..., description="Symbol to add e.g. BTC/USD")) -> Dict[str, List[str]]:
    """Add a symbol to the watchlist."""
    sym = symbol.upper()
    if sym not in watchlist:
        watchlist.append(sym)
    return {"watchlist": watchlist}


@app.post("/api/watchlist/remove")
async def remove_symbol(symbol: str = Query(..., description="Symbol to remove")) -> Dict[str, List[str]]:
    """Remove a symbol from the watchlist."""
    sym = symbol.upper()
    try:
        watchlist.remove(sym)
    except ValueError:
        pass
    return {"watchlist": watchlist}


@app.get("/api/account/snapshot")
async def get_account_snapshot() -> Dict[str, float]:
    """Return a snapshot of the account's current state."""
    return account_snapshot


@app.get("/api/performance/today")
async def get_performance_today() -> Dict[str, float]:
    """Return today's performance metrics."""
    return performance_today


@app.get("/api/trades/last")
async def get_last_trade() -> Dict[str, Optional[str]]:
    """Return details about the most recent trade."""
    return last_trade


@app.post("/api/settings/risk")
async def update_risk(
    position_size: Optional[float] = Query(None, description="Maximum position size per trade"),
    max_daily_loss: Optional[float] = Query(None, description="Maximum daily loss before the bot auto‑stops"),
    stop_loss_pct: Optional[float] = Query(None, description="Stop loss percentage (e.g. 0.05 for 5%)"),
) -> Dict[str, float]:
    """Update risk control settings."""
    if position_size is not None:
        settings["position_size"] = position_size
    if max_daily_loss is not None:
        settings["max_daily_loss"] = max_daily_loss
    if stop_loss_pct is not None:
        settings["stop_loss_pct"] = stop_loss_pct
    return settings

# ---------------------------------------------------------------------------
# Additional endpoints for live pricing, manual trading and session control
# ---------------------------------------------------------------------------

@app.get("/api/prices")
async def get_prices(symbols: Optional[str] = Query(None, description="Comma-separated list of symbols e.g. BTC/USD,ETH/USD")) -> Dict[str, float]:
    """Return current USD prices for the given symbols or the entire watchlist.

    If no symbols are provided, all symbols in the current watchlist are used.
    Symbols without a known mapping or price will be omitted from the result.
    """
    if symbols:
        syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        syms = list(set(watchlist))
    return fetch_live_prices(syms)


@app.post("/api/trade")
async def manual_trade(
    symbol: str = Query(..., description="Symbol to trade, e.g. BTC/USD"),
    side: str = Query(..., description="BUY or SELL"),
    quantity: float = Query(..., description="Quantity in units of the base asset (e.g. 0.1)")
) -> Dict[str, str]:
    """Execute a manual trade in the paper account.

    This endpoint uses live prices if available. It will adjust the cash balance,
    positions and log the trade. For BUY orders, if there is insufficient cash
    an error is returned. For SELL orders, if there is insufficient position
    quantity an error is returned.
    """
    sym = symbol.upper()
    side_u = side.upper()
    if side_u not in {"BUY", "SELL"}:
        return {"error": "side must be BUY or SELL"}
    # Fetch current price
    price_map = fetch_live_prices([sym])
    price = price_map.get(sym)
    if price is None or price <= 0:
        return {"error": "Unknown price for symbol"}
    # Ensure quantity is positive
    if quantity <= 0:
        return {"error": "quantity must be positive"}
    # BUY logic
    if side_u == "BUY":
        cost = quantity * price
        if account_snapshot["cash"] < cost:
            return {"error": "insufficient cash"}
        account_snapshot["cash"] -= cost
        positions[sym] = positions.get(sym, 0.0) + quantity
        performance_today["trades_count"] += 1
        trade_log.append(
            {
                "time": datetime.datetime.utcnow().isoformat(),
                "symbol": sym,
                "side": "BUY",
                "quantity": f"{quantity}",
                "price": f"{price}",
            }
        )
    else:  # SELL
        pos_qty = positions.get(sym, 0.0)
        if pos_qty < quantity:
            return {"error": "insufficient position quantity"}
        proceeds = quantity * price
        positions[sym] = pos_qty - quantity
        account_snapshot["cash"] += proceeds
        performance_today["trades_count"] += 1
        trade_log.append(
            {
                "time": datetime.datetime.utcnow().isoformat(),
                "symbol": sym,
                "side": "SELL",
                "quantity": f"{quantity}",
                "price": f"{price}",
            }
        )
    # Update account snapshot with new positions and equity
    update_account_snapshot()
    return {"status": "ok"}


@app.post("/api/reset")
async def reset_account(balance: Optional[float] = Query(None, description="New starting balance (optional)")) -> Dict[str, float]:
    """Reset the paper account to an empty state.

    Clears positions, trade log, PnL and resets cash to the specified balance or
    the default initial_balance. This does not stop the price feed if it is
    running; use the kill endpoint for that.
    """
    global positions, trade_log, performance_today, account_snapshot, initial_balance
    positions = {}
    trade_log = []
    performance_today = {
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "trades_count": 0,
    }
    if balance is not None:
        # Validate balance
        try:
            bal_val = float(balance)
            initial_balance = bal_val
        except Exception:
            bal_val = initial_balance
    else:
        bal_val = initial_balance
    account_snapshot = {
        "equity": bal_val,
        "cash": bal_val,
        "positions_value": 0.0,
        "unrealized_pnl": 0.0,
    }
    return account_snapshot


@app.get("/api/trades")
async def get_trade_log() -> Dict[str, List[Dict[str, str]]]:
    """Return the list of executed trades."""
    return {"trades": trade_log}

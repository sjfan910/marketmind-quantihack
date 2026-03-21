"""
api.py — FastAPI dashboard data API.

Exposes:
  GET /portfolio  — current holdings, PnL, cash (proxied from exchange)
  GET /trades     — recent trade log with reasoning
  GET /status     — current mode, volatility, spread

Run with: uvicorn api:app --host 0.0.0.0 --port 8000
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from config import config
from events import emergency_handler
from logger import trade_logger

app = FastAPI(title="Quantihack Trading Dashboard", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# We lazily import the engine state to avoid circular imports at startup.
# In production, run engine.py and api.py as separate processes sharing trades.jsonl.


@app.get("/portfolio")
def get_portfolio() -> dict:
    """
    Returns current portfolio snapshot by reading the most recent log entry.
    For live data, wire this up to a shared state store or the exchange client directly.
    """
    records = trade_logger.recent(limit=1)
    if not records:
        return {"cash": 0.0, "inventory": 0.0, "pnl": 0.0, "message": "no data yet"}
    latest = records[-1]
    return {
        "inventory": latest.get("inventory_after", 0.0),
        "pnl": latest.get("pnl_cumulative", 0.0),
        "last_updated": latest.get("timestamp"),
    }


@app.get("/trades")
def get_trades(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    """Returns the N most recent trade log entries."""
    return trade_logger.recent(limit=limit)


@app.get("/status")
def get_status() -> dict:
    """Returns current engine mode and key metrics."""
    records = trade_logger.recent(limit=1)
    if not records:
        latest_vol = 0.0
        latest_spread = 0.0
        latest_pnl = 0.0
    else:
        r = records[-1]
        latest_vol = r.get("volatility", 0.0)
        latest_spread = r.get("spread", 0.0)
        latest_pnl = r.get("pnl_cumulative", 0.0)

    return {
        "mode": "HALTED" if emergency_handler.is_halted else "ACTIVE",
        "asset": config.ASSET,
        "latest_volatility": latest_vol,
        "current_spread_pct": latest_spread * 100,
        "pnl_cumulative": latest_pnl,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/resume")
def manual_resume() -> dict:
    """Human operator endpoint: manually resume trading after an emergency halt."""
    if not emergency_handler.is_halted:
        return {"status": "ok", "message": "Engine is not currently halted."}
    emergency_handler.manual_resume()
    return {"status": "ok", "message": "Trading resumed by operator."}

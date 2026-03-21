"""
logger.py — Structured JSON trade logger.

Every trade decision is recorded as a JSON line in trades.jsonl.
The `reasoning` field is designed to be human-readable for the Stage 2 demo.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from config import config

ActionType = Literal["BUY", "SELL", "QUOTE_UPDATE", "HALT", "RESUME", "EMERGENCY"]


class TradeLogger:
    def __init__(self, path: str = config.TRADE_LOG_PATH) -> None:
        self._path = Path(path)

    def log(
        self,
        action: ActionType,
        asset: str,
        price: float,
        quantity: float,
        reasoning: str,
        inventory_before: float,
        inventory_after: float,
        spread: float,
        volatility: float,
        pnl_cumulative: float,
    ) -> None:
        """Append one structured trade record to the JSONL log file."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "asset": asset,
            "price": price,
            "quantity": quantity,
            "reasoning": reasoning,
            "inventory_before": inventory_before,
            "inventory_after": inventory_after,
            "spread": spread,
            "volatility": volatility,
            "pnl_cumulative": pnl_cumulative,
        }
        line = json.dumps(record)
        with self._path.open("a") as f:
            f.write(line + "\n")
        # Also echo to stdout for real-time monitoring
        print(f"[LOG] {record['timestamp']} {action:14s} {asset} px={price:.4f} "
              f"qty={quantity} inv={inventory_after:.2f} pnl={pnl_cumulative:.2f}",
              file=sys.stderr)

    def recent(self, limit: int = 50) -> list[dict]:
        """Return the most recent N log records (newest last)."""
        if not self._path.exists():
            return []
        lines = self._path.read_text().splitlines()
        return [json.loads(l) for l in lines[-limit:]]


# Singleton logger
trade_logger = TradeLogger()

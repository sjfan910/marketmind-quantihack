"""
config.py — All tunable parameters for the market-making engine.
Loaded from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    # Spread & quoting
    BASE_SPREAD: float = float(os.getenv("BASE_SPREAD", "0.02"))          # 2% base spread
    SKEW_COEFFICIENT: float = float(os.getenv("SKEW_COEFFICIENT", "0.001"))  # per-unit inventory skew

    # Volatility regime
    VOLATILITY_WINDOW: int = int(os.getenv("VOLATILITY_WINDOW", "20"))       # rolling window (ticks)
    VOLATILITY_MULTIPLIER: float = float(os.getenv("VOLATILITY_MULTIPLIER", "2.0"))  # widen spread above 2x vol
    SPREAD_WIDEN_FACTOR: float = float(os.getenv("SPREAD_WIDEN_FACTOR", "3.0"))      # widen by 3x in high-vol

    # Inventory & sizing
    MAX_INVENTORY: float = float(os.getenv("MAX_INVENTORY", "10.0"))    # halt quoting beyond this
    LOT_SIZE: float = float(os.getenv("LOT_SIZE", "1.0"))              # base order size
    INVENTORY_SCALE_THRESHOLD: float = float(os.getenv("INVENTORY_SCALE_THRESHOLD", "5.0"))  # start scaling down

    # Emergency / halting
    HALT_THRESHOLD: float = float(os.getenv("HALT_THRESHOLD", "0.02"))    # 2% move in 5 ticks triggers halt
    HALT_TICKS: int = int(os.getenv("HALT_TICKS", "5"))                   # ticks window for dislocation check
    RESUME_THRESHOLD: float = float(os.getenv("RESUME_THRESHOLD", "0.5")) # resume when vol < 0.5x avg
    RESUME_TICKS: int = int(os.getenv("RESUME_TICKS", "10"))             # must be calm for this many ticks

    # PnL alert threshold
    PNL_DRAWDOWN_ALERT: float = float(os.getenv("PNL_DRAWDOWN_ALERT", "0.10"))  # alert if -10% from peak

    # Notifications
    ALERT_WEBHOOK: Optional[str] = os.getenv("ALERT_WEBHOOK")  # Discord/Slack webhook URL

    # Logging
    TRADE_LOG_PATH: str = os.getenv("TRADE_LOG_PATH", "trades.jsonl")

    # Exchange connectivity (adapt to Quantihack platform)
    EXCHANGE_URL: str = os.getenv("EXCHANGE_URL", "http://localhost:8080")
    EXCHANGE_API_KEY: str = os.getenv("EXCHANGE_API_KEY", "")
    ASSET: str = os.getenv("ASSET", "STOCK")

    # Polling interval (seconds) between market-making cycles
    LOOP_INTERVAL: float = float(os.getenv("LOOP_INTERVAL", "1.0"))

    # Dashboard API
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "8000"))


# Singleton config instance
config = Config()

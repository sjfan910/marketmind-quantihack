"""
events.py — Emergency event handler.

Monitors for sudden price dislocations and triggers a halt + human notification.
Resumes only when volatility has been calm for RESUME_TICKS consecutive ticks,
or when a manual resume signal is issued.
"""

import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import config
from logger import trade_logger, ActionType


class EmergencyHandler:
    """
    Detects abnormal market conditions and halts the engine.

    Dislocation rule: if the mid-price moves more than HALT_THRESHOLD (e.g. 2%)
    within HALT_TICKS consecutive ticks, treat it as an emergency.
    """

    def __init__(self) -> None:
        self._price_history: deque[float] = deque(maxlen=config.HALT_TICKS + 1)
        self._halted: bool = False
        self._calm_ticks: int = 0          # consecutive ticks below RESUME_THRESHOLD
        self._halt_reason: str = ""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_halted(self) -> bool:
        return self._halted

    def update(self, mid_price: float, current_volatility: float, avg_volatility: float) -> None:
        """
        Called every tick. Updates internal state and triggers halt if needed.
        Does NOT resume automatically — see try_auto_resume().
        """
        self._price_history.append(mid_price)
        self._check_dislocation(mid_price)
        self._check_volatility_spike(current_volatility, avg_volatility)

    def try_auto_resume(self, current_volatility: float, avg_volatility: float) -> bool:
        """
        Returns True if auto-resume conditions are met and engine can restart.
        Counts calm ticks and resumes after RESUME_TICKS consecutive calm ticks.
        """
        if not self._halted:
            return False

        threshold = avg_volatility * config.RESUME_THRESHOLD
        if current_volatility <= threshold:
            self._calm_ticks += 1
        else:
            self._calm_ticks = 0

        if self._calm_ticks >= config.RESUME_TICKS:
            self._resume("auto: volatility subsided for "
                         f"{config.RESUME_TICKS} consecutive ticks")
            return True
        return False

    def manual_resume(self) -> None:
        """Human operator manually resumes trading."""
        self._resume("manual: operator resumed trading")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_dislocation(self, mid_price: float) -> None:
        """Check for a rapid large price move across the history window."""
        if len(self._price_history) < 2:
            return
        oldest = self._price_history[0]
        if oldest == 0:
            return
        move = abs(mid_price - oldest) / oldest
        if move >= config.HALT_THRESHOLD:
            reason = (f"price dislocation detected: {move*100:.2f}% move in "
                      f"{len(self._price_history)-1} ticks "
                      f"(oldest={oldest:.4f}, current={mid_price:.4f})")
            self._trigger_halt(reason)

    def _check_volatility_spike(self, current_vol: float, avg_vol: float) -> None:
        """Halt if current volatility is extremely elevated (>5x avg)."""
        if avg_vol <= 0:
            return
        ratio = current_vol / avg_vol
        if ratio > 5.0 and not self._halted:
            reason = (f"extreme volatility spike: current={current_vol:.6f} "
                      f"is {ratio:.1f}x the rolling average ({avg_vol:.6f})")
            self._trigger_halt(reason)

    def _trigger_halt(self, reason: str) -> None:
        if self._halted:
            return  # already halted
        self._halted = True
        self._calm_ticks = 0
        self._halt_reason = reason
        msg = f"[EMERGENCY] {datetime.now(timezone.utc).isoformat()} — {reason}"
        print(msg, flush=True)
        self._send_webhook(msg)
        trade_logger.log(
            action="EMERGENCY",
            asset=config.ASSET,
            price=0.0,
            quantity=0.0,
            reasoning=f"HALT triggered: {reason}",
            inventory_before=0.0,
            inventory_after=0.0,
            spread=0.0,
            volatility=0.0,
            pnl_cumulative=0.0,
        )

    def _resume(self, reason: str) -> None:
        self._halted = False
        self._calm_ticks = 0
        msg = f"[RESUME] {datetime.now(timezone.utc).isoformat()} — {reason}"
        print(msg, flush=True)
        self._send_webhook(msg)
        trade_logger.log(
            action="RESUME",
            asset=config.ASSET,
            price=0.0,
            quantity=0.0,
            reasoning=f"Trading resumed: {reason}",
            inventory_before=0.0,
            inventory_after=0.0,
            spread=0.0,
            volatility=0.0,
            pnl_cumulative=0.0,
        )

    def _send_webhook(self, message: str) -> None:
        """Best-effort webhook notification to Discord/Slack."""
        url = config.ALERT_WEBHOOK
        if not url:
            return
        try:
            payload = {"text": message, "content": message}
            httpx.post(url, json=payload, timeout=5.0)
        except Exception as exc:
            print(f"[WARN] webhook delivery failed: {exc}", file=sys.stderr)


# Singleton handler
emergency_handler = EmergencyHandler()

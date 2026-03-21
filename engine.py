"""
engine.py — Core market-making engine.

Strategy:
  - Post symmetric bid/ask around the mid-price
  - Skew quotes based on inventory to manage directional risk
  - Widen spreads in high-volatility regimes
  - Delegate emergency detection to events.py
  - Log every decision to logger.py

Exchange connectivity is abstracted behind ExchangeClient (adapt to Quantihack API).
"""

import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import config
from events import emergency_handler
from logger import trade_logger


# ---------------------------------------------------------------------------
# Exchange client — adapt this section to the Quantihack platform API
# ---------------------------------------------------------------------------

@dataclass
class OrderBook:
    best_bid: float
    best_ask: float
    mid: float


@dataclass
class Portfolio:
    cash: float
    inventory: float   # positive = long, negative = short
    pnl: float


class ExchangeClient:
    """
    Thin wrapper around the Quantihack exchange HTTP API.
    Replace the placeholder URLs and payloads with the real platform spec.
    """

    def __init__(self) -> None:
        self._base = config.EXCHANGE_URL.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {config.EXCHANGE_API_KEY}",
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=5.0, headers=self._headers)

    # -- Market data --

    def get_order_book(self, asset: str) -> OrderBook:
        """Fetch current best bid/ask. Adapt endpoint to platform."""
        resp = self._client.get(f"{self._base}/orderbook/{asset}")
        resp.raise_for_status()
        data = resp.json()
        # Adapt field names to the actual API response
        best_bid = float(data["bid"])
        best_ask = float(data["ask"])
        mid = (best_bid + best_ask) / 2.0
        return OrderBook(best_bid=best_bid, best_ask=best_ask, mid=mid)

    def get_portfolio(self) -> Portfolio:
        """Fetch current portfolio state."""
        resp = self._client.get(f"{self._base}/portfolio")
        resp.raise_for_status()
        data = resp.json()
        return Portfolio(
            cash=float(data.get("cash", 0.0)),
            inventory=float(data.get("inventory", 0.0)),
            pnl=float(data.get("pnl", 0.0)),
        )

    # -- Order management --

    def cancel_all_orders(self, asset: str) -> None:
        """Cancel all open orders for this asset."""
        try:
            self._client.delete(f"{self._base}/orders/{asset}")
        except Exception as exc:
            print(f"[WARN] cancel_all_orders failed: {exc}", file=sys.stderr)

    def place_limit_order(
        self,
        asset: str,
        side: str,   # "BUY" or "SELL"
        price: float,
        quantity: float,
    ) -> Optional[str]:
        """Place a limit order. Returns order ID if successful."""
        payload = {
            "asset": asset,
            "side": side,
            "type": "LIMIT",
            "price": round(price, 4),
            "quantity": round(quantity, 4),
        }
        try:
            resp = self._client.post(f"{self._base}/orders", json=payload)
            resp.raise_for_status()
            return resp.json().get("order_id")
        except Exception as exc:
            print(f"[WARN] place_limit_order failed ({side} {quantity}@{price}): {exc}",
                  file=sys.stderr)
            return None

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Volatility tracker
# ---------------------------------------------------------------------------

class VolatilityTracker:
    """Tracks rolling std-dev of mid-price returns."""

    def __init__(self, window: int = config.VOLATILITY_WINDOW) -> None:
        self._window = window
        self._mid_history: deque[float] = deque(maxlen=window + 1)
        self._returns: deque[float] = deque(maxlen=window)

    def update(self, mid: float) -> tuple[float, float]:
        """
        Update with a new mid-price.
        Returns (current_volatility, rolling_avg_volatility).
        current_volatility = std-dev of recent returns (last N ticks).
        rolling_avg_volatility = mean of all stored return magnitudes (proxy for baseline).
        """
        if self._mid_history:
            prev = self._mid_history[-1]
            if prev > 0:
                ret = abs(mid - prev) / prev
                self._returns.append(ret)
        self._mid_history.append(mid)

        n = len(self._returns)
        if n < 2:
            return 0.0, 0.0

        mean = sum(self._returns) / n
        variance = sum((r - mean) ** 2 for r in self._returns) / n
        current_vol = variance ** 0.5

        # Rolling average = simple mean of stored return magnitudes
        avg_vol = mean
        return current_vol, avg_vol

    @property
    def is_high_volatility(self) -> bool:
        if not self._returns:
            return False
        n = len(self._returns)
        if n < 2:
            return False
        mean = sum(self._returns) / n
        variance = sum((r - mean) ** 2 for r in self._returns) / n
        current_vol = variance ** 0.5
        avg_vol = mean
        return avg_vol > 0 and current_vol > config.VOLATILITY_MULTIPLIER * avg_vol


# ---------------------------------------------------------------------------
# Market-making engine
# ---------------------------------------------------------------------------

class MarketMaker:
    """
    Main market-making loop.

    Each iteration:
      1. Fetch order book & portfolio state
      2. Update volatility tracker & emergency handler
      3. If halted: attempt auto-resume, skip quoting
      4. Compute bid/ask prices with inventory skew + vol adjustment
      5. Cancel stale quotes, post new quotes
      6. Log the decision
    """

    def __init__(self) -> None:
        self._exchange = ExchangeClient()
        self._vol_tracker = VolatilityTracker()
        self._peak_pnl: float = 0.0
        self._active_bid_id: Optional[str] = None
        self._active_ask_id: Optional[str] = None

    # -- Main loop --

    def run(self) -> None:
        print(f"[ENGINE] Market maker starting. Asset={config.ASSET}, "
              f"spread={config.BASE_SPREAD*100:.1f}%, lot={config.LOT_SIZE}",
              flush=True)
        try:
            while True:
                try:
                    self._tick()
                except Exception as exc:
                    print(f"[ERROR] Unhandled exception in tick: {exc}", file=sys.stderr)
                    print(f"[EMERGENCY] {datetime.now(timezone.utc).isoformat()} "
                          f"— unhandled exception: {exc}", flush=True)
                time.sleep(config.LOOP_INTERVAL)
        finally:
            self._exchange.close()

    # -- Single tick --

    def _tick(self) -> None:
        book = self._exchange.get_order_book(config.ASSET)
        portfolio = self._exchange.get_portfolio()

        current_vol, avg_vol = self._vol_tracker.update(book.mid)

        # Update peak PnL and check drawdown
        if portfolio.pnl > self._peak_pnl:
            self._peak_pnl = portfolio.pnl
        self._check_pnl_alert(portfolio.pnl)

        # Feed emergency handler
        emergency_handler.update(book.mid, current_vol, avg_vol)

        if emergency_handler.is_halted:
            # Cancel any lingering open orders
            self._exchange.cancel_all_orders(config.ASSET)
            # Attempt auto-resume
            emergency_handler.try_auto_resume(current_vol, avg_vol)
            return

        # Determine effective spread
        high_vol = avg_vol > 0 and current_vol > config.VOLATILITY_MULTIPLIER * avg_vol
        spread = (config.BASE_SPREAD * config.SPREAD_WIDEN_FACTOR
                  if high_vol else config.BASE_SPREAD)

        # Inventory skew: if long → lower bid, if short → raise ask
        inventory = portfolio.inventory
        skew = inventory * config.SKEW_COEFFICIENT

        bid_price = book.mid * (1.0 - spread / 2.0 - skew)
        ask_price = book.mid * (1.0 + spread / 2.0 - skew)

        # Ensure bid < ask (sanity check)
        if bid_price >= ask_price:
            bid_price = book.mid * (1.0 - spread / 2.0)
            ask_price = book.mid * (1.0 + spread / 2.0)

        # Scale lot size down as inventory approaches MAX_INVENTORY
        lot = self._adjusted_lot(inventory)

        # If inventory is at max, only quote on the reducing side
        quote_bid = abs(inventory) < config.MAX_INVENTORY or inventory < 0
        quote_ask = abs(inventory) < config.MAX_INVENTORY or inventory > 0

        # Cancel stale quotes and post fresh ones
        self._exchange.cancel_all_orders(config.ASSET)

        reasoning = self._build_reasoning(
            book, portfolio, spread, high_vol, skew, lot, current_vol, avg_vol
        )

        if quote_bid:
            self._active_bid_id = self._exchange.place_limit_order(
                config.ASSET, "BUY", bid_price, lot
            )
        if quote_ask:
            self._active_ask_id = self._exchange.place_limit_order(
                config.ASSET, "SELL", ask_price, lot
            )

        trade_logger.log(
            action="QUOTE_UPDATE",
            asset=config.ASSET,
            price=book.mid,
            quantity=lot,
            reasoning=reasoning,
            inventory_before=inventory,
            inventory_after=inventory,   # inventory updates when fills arrive
            spread=spread,
            volatility=current_vol,
            pnl_cumulative=portfolio.pnl,
        )

    # -- Helpers --

    def _adjusted_lot(self, inventory: float) -> float:
        """Scale lot size down linearly once inventory exceeds the threshold."""
        threshold = config.INVENTORY_SCALE_THRESHOLD
        max_inv = config.MAX_INVENTORY
        abs_inv = abs(inventory)
        if abs_inv <= threshold:
            return config.LOT_SIZE
        if abs_inv >= max_inv:
            return config.LOT_SIZE * 0.1   # minimum 10% lot
        scale = 1.0 - (abs_inv - threshold) / (max_inv - threshold) * 0.9
        return max(config.LOT_SIZE * scale, config.LOT_SIZE * 0.1)

    def _check_pnl_alert(self, pnl: float) -> None:
        """Alert if PnL drops more than PNL_DRAWDOWN_ALERT from peak."""
        if self._peak_pnl <= 0:
            return
        drawdown = (self._peak_pnl - pnl) / self._peak_pnl
        if drawdown >= config.PNL_DRAWDOWN_ALERT:
            msg = (f"[EMERGENCY] {datetime.now(timezone.utc).isoformat()} "
                   f"— PnL drawdown alert: {drawdown*100:.1f}% from peak "
                   f"(peak={self._peak_pnl:.2f}, current={pnl:.2f})")
            print(msg, flush=True)

    def _build_reasoning(
        self,
        book: OrderBook,
        portfolio: Portfolio,
        spread: float,
        high_vol: bool,
        skew: float,
        lot: float,
        current_vol: float,
        avg_vol: float,
    ) -> str:
        parts = [
            f"Mid={book.mid:.4f}; posting at spread={spread*100:.2f}%.",
        ]
        if high_vol:
            parts.append(
                f"High-volatility regime detected (vol={current_vol:.5f} vs avg={avg_vol:.5f}); "
                f"spread widened {config.SPREAD_WIDEN_FACTOR}x to {spread*100:.2f}%."
            )
        if abs(skew) > 1e-6:
            direction = "long" if portfolio.inventory > 0 else "short"
            parts.append(
                f"Inventory skew applied: {direction} position of {portfolio.inventory:.2f} units "
                f"→ skew={skew:.5f} (mid-price shifted to reduce exposure)."
            )
        if lot < config.LOT_SIZE:
            parts.append(
                f"Lot size reduced from {config.LOT_SIZE} to {lot:.3f} "
                f"due to elevated inventory ({portfolio.inventory:.2f})."
            )
        parts.append(f"PnL={portfolio.pnl:.2f}, cash={portfolio.cash:.2f}.")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    maker = MarketMaker()
    maker.run()

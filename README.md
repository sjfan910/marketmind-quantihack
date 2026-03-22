# Quantihack Autonomous Market-Making Agent

Autonomous quantitative trading system for the Quantihack simulated exchange competition (21–27 March 2026). Built to run continuously for 7 days with minimal human intervention, qualifying for the London finals on 28 March.

---

## Strategy

This is a **market-making** system, not a trend-follower. The edge comes from:

1. **Spread capture** — posting limit orders on both sides of the order book and collecting the bid-ask spread on every fill.
2. **Inventory management** — skewing quotes to offload accumulated positions before they become directional bets.
3. **Volatility regime awareness** — widening spreads during turbulent periods so the spread earned always exceeds the adverse-selection risk.

The system should never hold a large directional position. When inventory grows, the skew mechanism makes it progressively cheaper for the market to fill the engine out of that position.

---

## Architecture

```
config.py       — All tunable parameters, loaded from environment variables
engine.py       — Main market-making loop + exchange HTTP client
events.py       — Emergency detection, halt/resume logic, webhook alerts
logger.py       — Structured JSONL trade log with human-readable reasoning
api.py          — FastAPI dashboard (portfolio, trades, status, manual resume)
```

### Data flow

```
Exchange order book
        │
        ▼
  engine.py (tick loop)
        │
        ├──► events.py      checks for dislocations / vol spikes
        │         │
        │         └──► halt signal ──► cancel all orders
        │
        ├──► VolatilityTracker    rolling std-dev of mid-price returns
        │
        ├──► quote calculation    mid ± spread/2 ± inventory skew
        │
        ├──► ExchangeClient       cancel stale quotes, post new bid/ask
        │
        └──► logger.py            append structured record to trades.jsonl
                                          │
                                          └──► api.py reads trades.jsonl
                                                      for dashboard endpoints
```

---

## Components

### `config.py` — Configuration

All parameters live here. Every value has a sensible default and can be overridden via environment variable.

| Parameter | Default | Description |
|---|---|---|
| `BASE_SPREAD` | `0.02` | Symmetric spread around mid (2%) |
| `SKEW_COEFFICIENT` | `0.001` | Per-unit inventory price adjustment |
| `VOLATILITY_WINDOW` | `20` | Rolling window for std-dev calculation (ticks) |
| `VOLATILITY_MULTIPLIER` | `2.0` | Vol ratio above which spreads widen |
| `SPREAD_WIDEN_FACTOR` | `3.0` | Multiplier applied to spread in high-vol regime |
| `MAX_INVENTORY` | `10.0` | Absolute inventory limit; quoting on adding side stops |
| `LOT_SIZE` | `1.0` | Base order size |
| `INVENTORY_SCALE_THRESHOLD` | `5.0` | Inventory level where lot scaling begins |
| `HALT_THRESHOLD` | `0.02` | Price move (fraction) in `HALT_TICKS` that triggers emergency |
| `HALT_TICKS` | `5` | Ticks window for dislocation detection |
| `RESUME_THRESHOLD` | `0.5` | Vol must drop below `0.5 × avg` to count as calm |
| `RESUME_TICKS` | `10` | Consecutive calm ticks required for auto-resume |
| `PNL_DRAWDOWN_ALERT` | `0.10` | Alert operator if PnL falls 10% from peak |
| `LOOP_INTERVAL` | `1.0` | Seconds between market-making cycles |
| `ALERT_WEBHOOK` | _(unset)_ | Discord/Slack webhook URL for emergency alerts |
| `EXCHANGE_URL` | `http://localhost:8080` | Quantihack platform base URL |
| `EXCHANGE_API_KEY` | _(unset)_ | Platform API key |
| `ASSET` | `STOCK` | Tradeable asset identifier |

---

### `engine.py` — Market-Making Engine

**`ExchangeClient`** wraps the Quantihack HTTP API. Adapt field names in `get_order_book()`, `get_portfolio()`, and `place_limit_order()` to match the platform's actual response shapes.

**`VolatilityTracker`** computes a rolling standard deviation of mid-price returns over the last N ticks. It distinguishes between a *current* volatility (recent std-dev) and a *baseline* (rolling mean of return magnitudes). When current exceeds `VOLATILITY_MULTIPLIER × baseline`, the engine enters defensive mode.

**`MarketMaker.run()`** is the main loop:

1. Fetch order book → compute mid-price
2. Update volatility tracker and emergency handler
3. If halted: cancel orders, attempt auto-resume, skip quoting
4. Compute effective spread (base or widened)
5. Apply inventory skew: `skew = inventory × SKEW_COEFFICIENT`
   - Long inventory → lower bid (harder to buy more) + raise ask (easier to sell)
   - Short inventory → raise ask + lower bid symmetrically
6. Scale lot size down as inventory approaches `MAX_INVENTORY`
7. Cancel stale quotes, post fresh bid and ask
8. Log decision with reasoning

**Inventory skew formula:**

```
bid_price = mid × (1 - spread/2 - skew)
ask_price = mid × (1 + spread/2 - skew)
```

A positive inventory (long) produces a negative skew offset on the ask, making it more attractive for others to buy from you, while your bid is pulled away from mid to discourage accumulating more longs.

---

### `events.py` — Emergency Handler

Two independent triggers:

- **Price dislocation:** mid-price moves ≥ `HALT_THRESHOLD` (2%) within `HALT_TICKS` (5) consecutive ticks.
- **Extreme vol spike:** current volatility exceeds 5× the rolling average (separate from the spread-widening threshold).

On halt:
1. Prints `[EMERGENCY] <ISO timestamp> — <reason>` to stdout
2. Fires a POST to `ALERT_WEBHOOK` (if configured) with the same message — compatible with Discord and Slack incoming webhooks
3. Logs an `EMERGENCY` record to `trades.jsonl`
4. The engine cancels all open orders and stops quoting

**Auto-resume:** After a halt, the engine counts consecutive ticks where volatility is below `RESUME_THRESHOLD × avg_volatility`. Once `RESUME_TICKS` (10) calm ticks accumulate, trading restarts automatically.

**Manual resume:** Operator sends a `POST /resume` request to the API, or calls `emergency_handler.manual_resume()` directly.

---

### `logger.py` — Trade Logger

Every tick writes one JSON line to `trades.jsonl`:

```json
{
  "timestamp": "2026-03-22T14:03:21.847Z",
  "action": "QUOTE_UPDATE",
  "asset": "STOCK",
  "price": 100.42,
  "quantity": 1.0,
  "reasoning": "Mid=100.4200; posting at spread=2.00%. Inventory skew applied: long position of 3.00 units → skew=0.00300 (mid-price shifted to reduce exposure). PnL=12.50, cash=987.50.",
  "inventory_before": 3.0,
  "inventory_after": 3.0,
  "spread": 0.02,
  "volatility": 0.00041,
  "pnl_cumulative": 12.5
}
```

Action types: `BUY`, `SELL`, `QUOTE_UPDATE`, `HALT`, `RESUME`, `EMERGENCY`.

The `reasoning` field is plain English and genuinely descriptive — it will be used in the Stage 2 demo to illustrate explainable AI trading.

---

### `api.py` — Dashboard API

Run with `uvicorn api:app --port 8000` as a separate process. Reads `trades.jsonl` for data so it does not need to share memory with the engine process.

| Endpoint | Method | Description |
|---|---|---|
| `/portfolio` | GET | Latest inventory, PnL, last-updated timestamp |
| `/trades?limit=N` | GET | N most recent trade log entries (default 50, max 500) |
| `/status` | GET | Mode (ACTIVE/HALTED), latest volatility, spread, PnL |
| `/resume` | POST | Manually resume trading after an emergency halt |

---

## Setup & Running

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
export EXCHANGE_URL=https://exchange.quantihack.io   # adapt to actual platform URL
export EXCHANGE_API_KEY=your_api_key_here
export ASSET=STOCK                                   # adapt to actual asset name(s)
export ALERT_WEBHOOK=https://discord.com/api/webhooks/...   # optional
```

Optionally override any parameter from `config.py`:

```bash
export BASE_SPREAD=0.015          # tighten to 1.5%
export LOT_SIZE=2.0
export VOLATILITY_WINDOW=30
```

### 3. Adapt `ExchangeClient` to the platform

Before running, open [engine.py](engine.py) and update `ExchangeClient`:

- `get_order_book()` — match the field names returned by the platform's order book endpoint
- `get_portfolio()` — match cash/inventory/pnl field names
- `place_limit_order()` — match the order submission payload
- Endpoint paths (`/orderbook/{asset}`, `/orders`, `/portfolio`) — replace with actual paths

### 4. Start the engine

```bash
python engine.py
```

### 5. Start the dashboard (optional)

```bash
uvicorn api:app --host 0.0.0.0 --port 8000
```

Visit `http://localhost:8000/docs` for the auto-generated Swagger UI.

---

## Operating During the Competition

### Normal operation

The engine runs autonomously. Monitor `trades.jsonl` or the `/trades` endpoint for activity. Check `/status` to confirm mode is `ACTIVE`.

### When an emergency halt fires

1. You will see `[EMERGENCY]` printed to the engine's stdout (and a webhook notification if configured).
2. Check the `reasoning` field in the latest log entry for the cause.
3. Decide whether to wait for auto-resume (10 calm ticks) or manually resume via `POST /resume`.
4. **Do not restart the engine process** — the halt is a soft state, not a crash.

### Parameter tuning

After the first few hours of trading, review the log and adjust:

- If fills are rare → tighten `BASE_SPREAD`
- If inventory keeps accumulating in one direction → increase `SKEW_COEFFICIENT`
- If halts are too frequent → raise `HALT_THRESHOLD` or `VOLATILITY_MULTIPLIER`
- If halts are too rare → lower those thresholds

Restart the engine after changing env vars.

---

## Human Operator Responsibilities

| Situation | Action |
|---|---|
| `[EMERGENCY]` printed | Review reason; decide to wait for auto-resume or `POST /resume` |
| PnL drawdown alert | Check inventory; consider reducing `LOT_SIZE` via env var restart |
| Unhandled exception | Check logs; fix code; restart engine |
| Parameter override mid-competition | Update env vars; restart engine |
| Final deployment sign-off | Approve before starting on competition day |

Everything else is handled autonomously.

---

## Stage 2 Demo Notes

The `trades.jsonl` file is the primary demo artifact. Every record includes:
- A timestamp and action type
- The price, quantity, and resulting inventory
- A `reasoning` field in plain English explaining *why* the decision was made
- Volatility and spread context at the time of the decision

This makes the system self-documenting and directly demonstrates explainable AI trading to the Anthropic/Jane Street/Optiver judges.

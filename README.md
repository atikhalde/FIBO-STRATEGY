# FIBO-STRATEGY — NSE 0.0% + POC Live Scanner

Python scanner that replicates the TradingView indicator
**“Swing Fibonacci Arcs & Volume Profile [BigBeluga]”** (`Swing Fibonacci Arcs & Volume Profile.txt`)
and sends **2 live Telegram alerts** on the full NSE universe (Daily timeframe):

| # | Alert | Meaning |
|---|-------|---------|
| 🎯 1 | **TOUCHED 0.0% LEVEL** | Price wicked into the swing-anchor price (the `0.0%` label on your chart) |
| 🔥 2 | **TOUCHED POC LEVEL** | Price wicked into the Volume Profile Point of Control (yellow POC line) |

Universe filters (your spec): **full NSE, market-cap > ₹1,000 cr, last price ≥ ₹100.**

---

## 1. How the indicator works (deep analysis)

**Swing engine** (`swingLength = 70`)
- `H = highest(high, 70)`, `L = lowest(low, 70)` each bar.
- `high == H` → bullish leg; `low == L` → bearish leg (**low wins** if a bar is both).
- On a direction flip the opposite extreme is locked as the **swing anchor**
  (`swingPrice` @ `swingIndex`): a swing **LOW** anchors a bull leg, a swing **HIGH** anchors a bear leg.
- A dashed real-time leg tracks the running extreme of the current leg.

**0.0% level = the swing anchor price.** In the Pine source the `0.0%` “arc” is drawn with
radius 0 (`r0_x = 0, r0_y = 0`), so the label sits exactly at `(swingIndex, swingPrice)`.
Arc settings (`xScale`, `yScale`, `minDx`, `minDy`) only bend the curved arcs — they never
move the 0.0% price or the POC price, so alerts don’t need them.

**Volume Profile + POC** (recomputed on the last bar, from `swingIndex → last bar`)
- Price span split into `rows = int(clamp(span / (ATR(100)*0.3*0.5), 10, 100))` bins.
- Each bar’s volume is spread around its **close** with triangular weights
  `w = max(0, 1 − |close − mid| / (2·rowHeight))` (`nz(volume, 1.0)`).
- **POC = middle of the highest-volume bin** (first wins ties).

**Touch = exact wick containment: `low ≤ level ≤ high`. Zero tolerance** — exactly as drawn.

### The one scanner rule you must know (prior-bar anchor)

The 0.0% anchor is a *trailing extreme*: any daily bar that retraces all the way back to it
automatically becomes a new 70-bar extreme and **flips the swing on that same bar**, moving the
anchor to the opposite side. A naive “current anchor” check can therefore *never* fire
(verified: 0 hits in 900 test bars).

So Alert 1 fires when the bar touches the anchor **as drawn at the previous bar close**
— i.e. yesterday’s 0.0% line, the level you actually watched pre-market — as well as the
current anchor. The Telegram message tells you which one (`prev-bar anchor — swing flipped
on this bar`). Level *computation* is untouched and pixel-identical to TradingView.

---

## 2. Setup

```bash
git clone <this-repo> && cd FIBO-STRATEGY
pip install -r requirements.txt
cp config.example.env config.env   # then edit it (below)
```

### Telegram setup (2 minutes)

1. Message **@BotFather** → `/newbot` → copy the token → `TELEGRAM_BOT_TOKEN`.
2. Message your new bot anything (e.g. `hi`), then open in a browser:
   `https://api.telegram.org/bot<TOKEN>/getUpdates` → copy the `chat.id` → `TELEGRAM_CHAT_ID`.
   (For a channel: add the bot as admin, use `@channelname` or the `-100…` id.)
3. Test it:
   ```bash
   python scanner.py --send-test
   ```

---

## 3. Usage

```bash
# 0) Prove the engine matches Pine (offline, no internet needed)
python backtest_verify.py

# 1) One EOD-style scan of the last daily bar (prints report, no Telegram)
python scanner.py --mode once --limit 30 --no-refresh --dry-run

# 2) One scan + live Telegram alerts for today's touches
python scanner.py --mode once --telegram --summary

# 3) LIVE loop during market hours 9:15–15:30 IST (polls every 5 min)
python scanner.py --mode live --poll-sec 300
```

Useful flags: `--min-price 100 --min-mcap-cr 1000 --limit N --no-refresh
--include-today --dry-run --workers 4 --history 5y --env-file config.env`

### How LIVE mode works on the Daily timeframe

- Closed daily history is cached and refreshed every ~30 min.
- Every poll, **today’s developing daily bar** (aggregated from 1-minute bars, exactly like
  TradingView’s realtime daily bar) is merged onto history and the full Pine engine re-runs —
  so levels always match your chart *right now*.
- Each touch alerts **once**, then re-arms on a **new swing** or a **new trading day**
  (state in `state/alert_state.json`). No spam.

---

## 4. Files

| File | Purpose |
|------|---------|
| `engine.py` | Exact Pine port: swings, ATR, Volume Profile, POC, touch checks |
| `scanner.py` | `once` / `live` modes, market-hours loop, dedup, Telegram wiring |
| `datafeed.py` | Yahoo Finance: chunked daily batches + live 1m partials |
| `universe.py` | Full-NSE list + `MCAP > ₹1000cr`, `PRICE ≥ ₹100` filters (7-day mcap cache) |
| `alerts.py` | Telegram send + the 2 alert message templates |
| `backtest_verify.py` | 6 offline Pine-equivalence proofs (naive-loop VP, ATR, anchors, touch, history) |
| `nse_symbols.txt` | 520-symbol NSE seed list (auto-refreshed live from NSE/mirrors when reachable) |

---

## 5. Notes & limits (read this)

- **Yahoo is ~15 min delayed** and rate-limits aggressively on 2000-ticker pulls — that’s why
  downloads are chunked + threaded + cached. For tick-true live (WebSocket), the engine is
  broker-agnostic: point `datafeed.py` at Kite/Dhan/Upstox later without touching alert logic.
- First run builds the mcap cache (~10–20 min for the full universe); later runs reuse it for 7 days.
- Run `live` mode on a VPS/computer that stays on during market hours (9:15–15:30 IST, Mon–Fri).
  Outside hours it EOD-scans once and sleeps.
- Symbols that fail to download are skipped + logged — a few stale seeds are harmless.

## 6. Disclaimer

Educational tool, not investment advice. Delayed data can miss fast moves; always confirm on
your broker/TradingView chart before trading.

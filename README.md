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

# 4) Historical Backtest Scanner (CLI)
# Options: --touch [0.0|poc|both] --period [1y|2y|3y|5y] --universe [nifty50|nifty100|full]
python backtest_scanner.py --touch 0.0 --period 2y --universe nifty50
python backtest_scanner.py --touch poc --period 3y --universe nifty50
python backtest_scanner.py --touch both --period 5y --universe full

# 5) Historical Backtest Interactive Web Dashboard
python web_app.py   # open http://localhost:5000 in your browser
```

Useful flags: `--min-price 100 --min-mcap-cr 1000 --limit N --no-refresh
--include-today --dry-run --workers 4 --history 5y --env-file config.env
--report-file PATH --market-day-check`

### How LIVE mode works on the Daily timeframe

- Closed daily history is cached and refreshed every ~30 min.
- Every poll, **today’s developing daily bar** (aggregated from 1-minute bars, exactly like
  TradingView’s realtime daily bar) is merged onto history and the full Pine engine re-runs —
  so levels always match your chart *right now*.
- Each touch alerts **once**, then re-arms on a **new swing** or a **new trading day**
  (state in `state/alert_state.json`). No spam. The dedup key is the **IST calendar day**, so a
  level that is touched all session alerts once that day, not 26 times.

---

## 3b. Run it on GitHub Actions (no VPS needed)

`.github/workflows/live-scanner.yml` runs the scanner on **live Yahoo Finance data** during NSE
hours, so nothing has to stay switched on at your end.

**Schedule (Mon–Fri, 26 runs/day, IST):**

| Cron (UTC) | IST | What |
|---|---|---|
| `45 3 * * 1-5` | 09:15 | opening scan |
| `0,15,30,45 4-9 * * 1-5` | 09:30 → 15:15 | every 15 min |
| `5 10 * * 1-5` | 15:35 | closing scan |

Each run: builds the universe → fetches closed daily history **+ today's developing bar** →
runs the Pine engine → Telegram alert per new touch → writes the report to the run's
**Summary** tab and uploads it as an artifact (`fibo-scan-<run-id>`, kept 7 days).

**Setup (once):**

1. Merge the workflow to your **default branch** — GitHub only fires `schedule` triggers from
   the default branch, so it will not run while it sits on a feature branch.
2. Add the two secrets under **Settings → Secrets and variables → Actions**:
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (same values as `config.env`).
3. Optional smoke test: **Actions → Live NSE scanner → Run workflow** with
   `dry_run = true`, `limit = 20`, `skip_holiday_check = true`. A dry run sends nothing and
   does not write alert state, so it cannot swallow a later real alert.

**Behaviour worth knowing:**

- **NSE holidays:** cron cannot know them, so each run first calls
  `python scanner.py --market-day-check` (exit `0` = trading, `10` = weekend/holiday) and skips
  the scan instead of re-reporting the previous session's levels. If Yahoo is unreachable the
  check fails *open* and the scan runs anyway — missing a real session is worse than a dry one.
- **State across runs:** `state/` (alert dedup) and `cache/` (market caps) are saved to the
  Actions cache and restored by the next run, so a touch alerts once and 2000 market-cap quotes
  aren't re-pulled every 15 minutes. GitHub evicts cache entries unused for 7 days, so after a
  long break the first run may re-alert once.
- **Runs never overlap:** `concurrency` queues the next slot instead of cancelling it. Job
  timeout is 20 min.
- **Runtime is unmeasured here** (this sandbox cannot reach Yahoo). A full-universe run has to
  pull 5y daily bars and 1m partials for ~1000+ symbols; if runs start hitting the 20-min
  timeout, widen the cron to every 30 min (`0,30 4-9 * * 1-5`) or pass `--no-refresh`.
- Manual triggers expose `history`, `limit`, `workers`, `dry_run`, `no_refresh`,
  `skip_holiday_check`.

---

## 3c. Historical Backtest Scanner & PDF Report Generator

Scan historical performance across NSE stocks with exact TradingView Pine indicator logic:

### Live-Scanner Parity (bull side only):
The backtest replicates the live scanner (`scanner.py` + `engine.py`) exactly:
- **Bull-side LONG only:** only bullish-leg signals (anchor = swing LOW support) are evaluated — every simulated trade is a LONG. Bear-leg SHORT simulation is excluded.
- **Same signal rules:** 0.0% touch of the current *or* prior-bar drawn anchor (including flip bars), POC wick touch, zero-tolerance `low <= level <= high`, causal bar-by-bar engine.
- **No trade cooldown:** the live scanner re-arms every trading day, so consecutive-day touches each produce a trade.
- **5-year context:** the engine always runs on 5y of daily history like the live scanner's `--history 5y` default; trades/touches are reported only inside the selected period.
- **Consistent counts:** all touch counts in CLI/PDF/web reports count bull-leg rows only, matching the trade log.

### Options Available:
1. **Touch Condition (2 Core Options + Both):**
   - `0.0% touches`: Price touches the swing-anchor level (swing LOW support on a bull leg, including prior-bar anchor rule).
   - `poc touches`: Price wicks into the Volume Profile Point of Control line.
   - `both`: Evaluates all qualifying touches (bull side only).
2. **Backtest Durations:**
   - `1yr` (1 year / ~252 trading days)
   - `2yr` (2 years / ~504 trading days)
   - `3yr` (3 years / ~756 trading days)
   - `5yr` (5 years / ~1260 trading days)
3. **Stock Universes:**
   - `nifty50`: Top 50 liquid Indian large caps.
   - `nifty100`: Top 100 benchmark constituents.
   - `full`: Full filtered NSE universe (>₹1,000 Cr market cap, price ≥ ₹100).
   - Custom symbols: `--symbols RELIANCE,TCS,INFY,HDFCBANK`.
4. **Institutional PDF Report:**
   - Auto-generated upon run completion with executive KPI cards, 4-panel visual charts (Equity Curve, Forward Return Horizons, Return Distribution, 0.0% vs POC comparison), stock performance rankings, and trade event log.

### CLI Examples:
```bash
# 2-year backtest on 0.0% touches for Nifty 50
python backtest_scanner.py --touch 0.0 --period 2y --universe nifty50

# 3-year backtest on POC touches for Nifty 50
python backtest_scanner.py --touch poc --period 3y --universe nifty50

# 5-year full universe backtest with custom PDF destination
python backtest_scanner.py --touch both --period 5y --universe full --pdf reports/fibo_5y_report.pdf

# Interactive terminal wizard
python backtest_scanner.py --interactive
```

### Interactive Web Dashboard:
```bash
python web_app.py
```
Open `http://localhost:5000` (or the live preview in Arena) to:
- Select touch options (0.0% touches, POC touches, Both) via radio buttons
- Pick backtest period (1yr, 2yr, 3yr, 5yr) with 1 click
- View live charts, stock rankings, and trade logs
- 1-click Download PDF Report and in-browser preview

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
| `backtest_scanner.py` | Historical backtest scanner CLI with interactive prompts and formatted output |
| `backtest_engine.py` | Fast causal historical scanner, trade simulation, horizon returns, metrics |
| `data_manager.py` | Universe baskets (Nifty 50/100/full), data loader with resilient fallback |
| `pdf_report.py` | Multi-page institutional PDF report generator (ReportLab + Matplotlib) |
| `web_app.py` | Interactive web dashboard (Flask) on `0.0.0.0:5000` with live preview |
| `test_backtest.py` | Unit and integration test suite verifying backtests and PDF compilation |
| `nse_symbols.txt` | 520-symbol NSE seed list (auto-refreshed live from NSE/mirrors when reachable) |
| `.github/workflows/live-scanner.yml` | Scheduled live scanner on GitHub Actions (NSE hours, every 15 min) |
| `.github/workflows/backtest-scanner.yml` | On-demand + weekly historical backtest on GitHub Actions (PDF artifact, no secrets needed) |

---

## 5. Notes & limits (read this)

- **Yahoo is ~15 min delayed** and rate-limits aggressively on 2000-ticker pulls — that’s why
  downloads are chunked + threaded + cached. For tick-true live (WebSocket), the engine is
  broker-agnostic: point `datafeed.py` at Kite/Dhan/Upstox later without touching alert logic.
- First run builds the mcap cache (~10–20 min for the full universe); later runs reuse it for 7 days.
- Run `live` mode on a VPS/computer that stays on during market hours (9:15–15:30 IST, Mon–Fri).
  Outside hours it EOD-scans once and sleeps. Don't have one? Use the
  GitHub Actions schedule instead (§3b) — it runs on GitHub's runners during market hours.
- Symbols that fail to download are skipped + logged — a few stale seeds are harmless.

## 6. Disclaimer

Educational tool, not investment advice. Delayed data can miss fast moves; always confirm on
your broker/TradingView chart before trading.

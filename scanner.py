#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════════════════════
# scanner.py — Live NSE scanner: 0.0% + POC touches → Telegram
# ══════════════════════════════════════════════════════════════════════════════
# Modes:
#   python scanner.py --mode once   single EOD-style scan of last daily bar
#   python scanner.py --mode live   live loop in market hours (9:15–15:30 IST)
#
# CI helpers (see .github/workflows/live-scanner.yml):
#   --report-file PATH        write the scan report to a file (Actions summary)
#   --market-day-check        exit 0 = NSE trading today, 10 = holiday/weekend
#
# Live-loop design (daily timeframe, exact Pine semantics):
#   • Closed daily history cached (refreshed every 30 min / on new day).
#   • Each poll: today's developing daily bar (from 1m bars) is merged onto
#     history → engine runs on the merged frame → this is EXACTLY what the
#     TradingView daily chart shows at that moment (realtime bar included).
#   • Touch = low <= level <= high on that merged bar. Zero tolerance.
#   • Dedup key: (symbol, level_type, swing_anchor_date) + once-per-day guard,
#     so a touch spams you ONCE, and re-arms on a new swing or a new day.
#
# Config via environment (or config.env file — see config.example.env):
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import scan_last_bar  # noqa: E402
from datafeed import fetch_daily_batch, fetch_today_partials, merge_partial  # noqa: E402
from universe import build_universe  # noqa: E402
from alerts import (format_scan_summary, format_touch_000, format_touch_poc,  # noqa: E402
                    now_ist, send_telegram)

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state" / "alert_state.json"
IST = ZoneInfo("Asia/Kolkata")
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fibo.scanner")


# ── config ────────────────────────────────────────────────────────────────────

def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


# ── market clock ──────────────────────────────────────────────────────────────

def market_status(now: Optional[datetime] = None) -> str:
    """'open' | 'pre' | 'post' | 'holiday-weekend' (weekday check only)."""
    now = now or datetime.now(IST)
    if now.weekday() >= 5:
        return "holiday-weekend"
    t = now.time()
    if t < MKT_OPEN:
        return "pre"
    if t > MKT_CLOSE:
        return "post"
    return "open"


# ── alert state (dedup) ───────────────────────────────────────────────────────

def load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:  # noqa: BLE001
        pass
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=1, default=str))
    except Exception as e:  # noqa: BLE001
        log.debug("state save failed: %s", e)


def should_alert(state: dict, symbol: str, kind: str,
                 swing_date, today: str) -> bool:
    """
    Alert once per (symbol, kind, swing-anchor). Re-arm when:
      • the swing anchor changed (new leg), or
      • a new trading day began (touch on a later bar is fresh information).
    """
    key = f"{symbol}|{kind}"
    anchor = str(pd.Timestamp(swing_date).date()) if swing_date is not None else "NA"
    prev = state.get(key)
    if prev and prev.get("anchor") == anchor and prev.get("day") == today:
        return False
    state[key] = {"anchor": anchor, "day": today,
                  "ts": datetime.now(IST).isoformat()}
    return True


# ── scan core ─────────────────────────────────────────────────────────────────

def evaluate(symbols: List[str], daily: Dict[str, pd.DataFrame],
             partials: Optional[Dict[str, dict]] = None,
             telegram: bool = False, token: str = "",
             chat: str = "", state: Optional[dict] = None,
             dry_run: bool = False) -> dict:
    """Run engine + touch checks over the universe. Returns stats + hits."""
    state = state if state is not None else {}
    today = datetime.now(IST).date().isoformat()
    hits_000: List[tuple] = []
    hits_poc: List[tuple] = []
    seen_000: List[tuple] = []   # touched, but already alerted earlier today
    seen_poc: List[tuple] = []
    errors = 0

    for sym in symbols:
        df = daily.get(sym)
        if df is None or len(df) < 5:
            continue
        try:
            frame = merge_partial(df, (partials or {}).get(sym)) if partials else df
            if frame is None or len(frame) < 5:
                continue
            res = scan_last_bar(frame, symbol=sym)
        except Exception as e:  # noqa: BLE001
            errors += 1
            log.debug("%s: engine failed: %s", sym, e)
            continue

        anchor_key = res["swing_date_prev"] if res.get("touched_000") == "prev-bar" else res["swing_date"]
        if res["touch_000"]:
            if should_alert(state, sym, "000", anchor_key, today):
                hits_000.append((sym, res))
                msg = format_touch_000(sym, res)
                log.info("🎯 %s touched 0.0%% @ %s", sym, res["level_000"])
                if telegram and not dry_run:
                    send_telegram(token, chat, msg)
                    time.sleep(0.4)  # Bot API rate kindness
            else:
                seen_000.append((sym, res))
        if res["touch_poc"]:
            if should_alert(state, sym, "POC", res["swing_date"], today):
                hits_poc.append((sym, res))
                msg = format_touch_poc(sym, res)
                log.info("🔥 %s touched POC @ %s", sym, res["poc"])
                if telegram and not dry_run:
                    send_telegram(token, chat, msg)
                    time.sleep(0.4)
            else:
                seen_poc.append((sym, res))

    return {"n": len(symbols), "hits_000": hits_000, "hits_poc": hits_poc,
            "seen_000": seen_000, "seen_poc": seen_poc,
            "errors": errors, "state": state}


def build_report(stats: dict) -> str:
    """Render the scan report as text (same text for console, file and CI)."""
    out: List[str] = []
    out.append("")
    out.append("=" * 72)
    out.append(f"FIBO SCAN — {now_ist()} | universe={stats['n']} "
               f"errors={stats['errors']}")
    out.append("=" * 72)
    for title, hits, key in (("TOUCHED 0.0% (swing anchor)", stats["hits_000"], "level_000"),
                             ("TOUCHED POC", stats["hits_poc"], "poc")):
        out.append(f"\n── {title}: {len(hits)} ──")
        for sym, r in hits:
            ld = r["last_date"].date() if r["last_date"] is not None else "?"
            out.append(f"  {sym:16s} {key}={r[key]:10.2f}  CMP={r['close']:10.2f}  "
                       f"bar={ld}  {r['direction'][:9]}")
    if not stats["hits_000"] and not stats["hits_poc"]:
        out.append("\n  (no NEW touches on this scan)")

    # Touched but already alerted earlier today: still worth showing on a
    # scheduled run's summary page, so a quiet report doesn't look like a
    # market that is nowhere near a level.
    seen = (("0.0%", stats.get("seen_000", []), "level_000"),
            ("POC", stats.get("seen_poc", []), "poc"))
    n_seen = sum(len(v) for _, v, _ in seen)
    if n_seen:
        out.append(f"\n── ALSO ON A LEVEL, ALERTED EARLIER TODAY (deduped): {n_seen} ──")
        for label, hits, key in seen:
            for sym, r in hits:
                out.append(f"  {sym:16s} {label:5s}={r[key]:10.2f}  "
                           f"CMP={r['close']:10.2f}")
    out.append("")
    return "\n".join(out)


def print_report(stats: dict) -> None:
    print(build_report(stats), end="")


def write_report(path, stats: dict) -> Optional[Path]:
    """Write the report to `path` (parents created). Returns the path or None."""
    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(build_report(stats), encoding="utf-8")
        log.info("report written → %s", p)
        return p
    except Exception as e:  # noqa: BLE001 — a report failure must not fail a scan
        log.warning("could not write report %s: %s", path, e)
        return None


# ── market-day guard (used by scheduled/CI runs) ──────────────────────────────

INDEX_PROBES = ("^NSEI", "RELIANCE.NS")


def market_day_check(now: Optional[datetime] = None):
    """
    (ok, reason) — is NSE probably trading right now?

    True when it is a weekday AND the newest daily bar Yahoo has is dated
    today (IST). Cron expressions cannot know NSE holidays, so scheduled runs
    call this first and skip the scan on a holiday instead of re-reporting the
    previous trading day's levels. If Yahoo is unreachable we say "ok" and let
    the scan itself decide — missing a real session is worse than a dry one.
    """
    now = now or datetime.now(IST)
    status = market_status(now)
    if status == "holiday-weekend":
        return False, f"{status} — {now:%a %d-%b-%Y} IST"
    for probe in INDEX_PROBES:
        try:
            frame = fetch_daily_batch([probe], period="1mo",
                                      max_workers=1).get(probe)
        except Exception as e:  # noqa: BLE001
            log.debug("market-day probe %s failed: %s", probe, e)
            frame = None
        if frame is None or len(frame) == 0:
            continue
        last = pd.Timestamp(frame.index[-1]).date()
        if last == now.date():
            return True, f"{probe} newest daily bar = {last} = today (IST)"
        return False, (f"{probe} newest daily bar = {last}, today = "
                       f"{now.date()} → NSE holiday or data lag")
    return True, "no price data reachable — running the scan anyway"


# ── modes ─────────────────────────────────────────────────────────────────────

def run_once(args) -> int:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if args.telegram and (not token or not chat):
        log.error("missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (see config.example.env)")
        return 2

    symbols = build_universe(min_price=args.min_price, min_mcap_cr=args.min_mcap_cr,
                             refresh_list=not args.no_refresh,
                             max_workers=args.workers, limit=args.limit)
    if not symbols:
        log.error("universe is empty — nothing to scan")
        return 1
    daily = fetch_daily_batch(symbols, period=args.history, max_workers=args.workers)

    partials = None
    if args.include_today:
        log.info("including today's developing bar (live partials)…")
        partials = fetch_today_partials(symbols, max_workers=args.workers)

    state = load_state()
    stats = evaluate(symbols, daily, partials, telegram=args.telegram,
                     token=token, chat=chat, state=state, dry_run=args.dry_run)
    if args.dry_run:
        log.info("dry-run: alert state NOT persisted (real runs stay armed)")
    else:
        save_state(stats["state"])
    print_report(stats)
    if getattr(args, "report_file", None):
        write_report(args.report_file, stats)
    if args.telegram and args.summary:
        send_telegram(token, chat, format_scan_summary(
            stats["n"], len(stats["hits_000"]), len(stats["hits_poc"]), stats["errors"]))
    return 0


def run_live(args) -> int:
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        log.error("LIVE mode needs TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (see config.example.env)")
        return 2

    log.info("building universe (price ≥ ₹%g, mcap > ₹%g cr)…",
             args.min_price, args.min_mcap_cr)
    symbols = build_universe(min_price=args.min_price, min_mcap_cr=args.min_mcap_cr,
                             refresh_list=not args.no_refresh,
                             max_workers=args.workers, limit=args.limit)
    if not symbols:
        log.error("universe is empty — nothing to scan")
        return 1
    send_telegram(token, chat,
                  f"🤖 <b>FIBO live scanner STARTED</b>\nUniverse: <b>{len(symbols)}</b> NSE stocks "
                  f"(price ≥ ₹{args.min_price:g}, mcap &gt; ₹{args.min_mcap_cr:g} cr)\n"
                  f"Timeframe: Daily | Poll: every {args.poll_sec}s | {now_ist()}")

    daily: Dict[str, pd.DataFrame] = {}
    last_daily_refresh = 0.0
    last_universe_day = datetime.now(IST).date()
    state = load_state()
    poll = 0

    while True:
        now = datetime.now(IST)
        status = market_status(now)

        if status == "holiday-weekend":
            log.info("weekend — sleeping 30 min…")
            time.sleep(1800)
            continue
        if status == "pre":
            log.info("pre-open — sleeping 5 min…")
            time.sleep(300)
            continue
        if status == "post":
            log.info("market closed — final EOD scan, then sleep till tomorrow…")
            daily = fetch_daily_batch(symbols, period=args.history, max_workers=args.workers)
            stats = evaluate(symbols, daily, None, telegram=True, token=token,
                             chat=chat, state=state)
            save_state(stats["state"])
            print_report(stats)
            if getattr(args, "report_file", None):
                write_report(args.report_file, stats)
            send_telegram(token, chat, "🌙 <b>FIBO live scanner sleeping</b> — market closed. "
                                      f"EOD touches today: 0.0%={len(stats['hits_000'])}, "
                                      f"POC={len(stats['hits_poc'])}. {now_ist()}")
            time.sleep(3600)
            continue

        # ── market OPEN ──
        poll += 1
        # new trading day → rebuild universe (fresh mcap/price eligibility)
        if now.date() != last_universe_day:
            last_universe_day = now.date()
            symbols = build_universe(min_price=args.min_price,
                                     min_mcap_cr=args.min_mcap_cr,
                                     refresh_list=False, max_workers=args.workers)
            daily = {}
        # refresh closed daily history every 30 min (cheap-ish, chunked)
        if not daily or (time.time() - last_daily_refresh) > 1800:
            daily = fetch_daily_batch(symbols, period=args.history, max_workers=args.workers)
            last_daily_refresh = time.time()

        log.info("── poll #%d (%s) — %d symbols ──", poll, now.strftime("%H:%M:%S"), len(symbols))
        try:
            partials = fetch_today_partials(symbols, max_workers=args.workers)
            stats = evaluate(symbols, daily, partials, telegram=True, token=token,
                             chat=chat, state=state)
            save_state(stats["state"])
            log.info("poll #%d: 0.0%%=%d POC=%d errors=%d", poll,
                     len(stats["hits_000"]), len(stats["hits_poc"]), stats["errors"])
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("poll #%d failed: %s", poll, e)

        time.sleep(args.poll_sec)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FIBO NSE scanner — 0.0%% + POC live alerts")
    ap.add_argument("--mode", choices=["once", "live"], default="once")
    ap.add_argument("--min-price", type=float, default=100.0)
    ap.add_argument("--min-mcap-cr", type=float, default=1000.0)
    ap.add_argument("--history", default="5y", help="daily history for levels")
    ap.add_argument("--limit", type=int, default=None, help="scan only first N symbols (testing)")
    ap.add_argument("--no-refresh", action="store_true", help="skip live NSE-list refresh")
    ap.add_argument("--include-today", action="store_true",
                    help="(once mode) merge today's developing bar like live mode")
    ap.add_argument("--telegram", action="store_true", help="(once mode) send Telegram alerts")
    ap.add_argument("--summary", action="store_true", help="also send scan-summary message")
    ap.add_argument("--dry-run", action="store_true", help="compute only, never send")
    ap.add_argument("--poll-sec", type=int, default=300, help="(live) seconds between polls")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--env-file", default="config.env")
    ap.add_argument("--report-file", default=None, metavar="PATH",
                    help="also write the scan report to PATH (CI summary page)")
    ap.add_argument("--market-day-check", action="store_true",
                    help="exit 0 if NSE is trading today (IST), 10 if not; no scan")
    ap.add_argument("--send-test", action="store_true", help="send a Telegram test then exit")
    args = ap.parse_args(argv)

    load_env_file(ROOT / args.env_file)

    if args.market_day_check:
        ok, reason = market_day_check()
        print(("TRADING DAY ✅  " if ok else "NOT A TRADING DAY ⏭️  ") + reason)
        return 0 if ok else 10

    if args.send_test:
        ok = send_telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""),
                           os.getenv("TELEGRAM_CHAT_ID", ""),
                           f"🤖 <b>FIBO scanner test OK</b> — {now_ist()}")
        print("telegram test:", "SENT ✅" if ok else "FAILED ❌")
        return 0 if ok else 1

    if args.mode == "live":
        try:
            return run_live(args)
        except KeyboardInterrupt:
            print("\nstopped by user.")
            return 0
    return run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())

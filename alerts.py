# ══════════════════════════════════════════════════════════════════════════════
# alerts.py — Telegram delivery + message formatting for the 2 alert types
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import html
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("fibo.alerts")
IST = ZoneInfo("Asia/Kolkata")


def now_ist(fmt: str = "%d-%b-%Y %H:%M:%S IST") -> str:
    return datetime.now(IST).strftime(fmt)


def send_telegram(bot_token: str, chat_id: str, text: str,
                  parse_mode: str = "HTML", timeout: int = 20) -> bool:
    """POST one message via Bot API. Returns True on success."""
    import requests
    if not bot_token or not chat_id:
        log.error("telegram not configured (missing token/chat id)")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text,
                                     "parse_mode": parse_mode,
                                     "disable_web_page_preview": True},
                          timeout=timeout)
        if r.status_code == 200:
            return True
        log.error("telegram %s: %s", r.status_code, r.text[:300])
        return False
    except Exception as e:  # noqa: BLE001
        log.error("telegram send failed: %s", e)
        return False


def _fmt(x: Optional[float], nd: int = 2) -> str:
    return "—" if x is None else f"₹{x:,.{nd}f}"


def _links(symbol_ns: str) -> str:
    base = symbol_ns.replace(".NS", "")
    tv = f"https://www.tradingview.com/chart/?symbol=NSE:{base}"
    nse = f"https://www.nseindia.com/get-quotes/equity?symbol={base}"
    return f'<a href="{tv}">TradingView</a> | <a href="{nse}">NSE</a>'


def format_touch_000(symbol_ns: str, res: dict) -> str:
    """ALERT 1 — price touched the 0.0% (swing anchor) level."""
    s = html.escape(symbol_ns.replace(".NS", ""))
    which = res.get("touched_000") or "current"
    if which == "prev-bar":
        lvl, sdt = res.get("level_000_prev"), res.get("swing_date_prev")
        tag = " (prev-bar anchor — swing flipped on this bar)"
    else:
        lvl, sdt = res.get("level_000"), res.get("swing_date")
        tag = ""
    return (
        f"🎯 <b>{s} — TOUCHED 0.0% LEVEL</b>{tag}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 0.0% (swing anchor): <b>{_fmt(lvl)}</b>\n"
        f"💰 CMP: <b>{_fmt(res['close'])}</b>  (H {_fmt(res['high'])} / L {_fmt(res['low'])})\n"
        f"📐 Leg: {html.escape(res['direction'])}\n"
        f"🕰️ Swing date: {sdt.date() if sdt is not None else '—'}\n"
        f"📊 Bar: {res['last_date'].date() if res['last_date'] is not None else '—'} (Daily)\n"
        f"⏰ {now_ist()}\n"
        f"{_links(symbol_ns)}"
    )


def format_touch_poc(symbol_ns: str, res: dict) -> str:
    """ALERT 2 — price touched the POC level."""
    s = html.escape(symbol_ns.replace(".NS", ""))
    return (
        f"🔥 <b>{s} — TOUCHED POC LEVEL</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📌 POC: <b>{_fmt(res['poc'])}</b>\n"
        f"💰 CMP: <b>{_fmt(res['close'])}</b>  (H {_fmt(res['high'])} / L {_fmt(res['low'])})\n"
        f"📐 Leg: {html.escape(res['direction'])}\n"
        f"🎯 0.0% ref: {_fmt(res['level_000'])}\n"
        f"📊 VP range: {_fmt(res['vp_pmin'])} – {_fmt(res['vp_pmax'])} ({res['vp_rows'] or '—'} rows)\n"
        f"📊 Bar: {res['last_date'].date() if res['last_date'] is not None else '—'} (Daily)\n"
        f"⏰ {now_ist()}\n"
        f"{_links(symbol_ns)}"
    )


def format_scan_summary(n_syms: int, n_000: int, n_poc: int, errors: int) -> str:
    return (f"🤖 <b>FIBO scan complete</b> — {now_ist()}\n"
            f"Universe: <b>{n_syms}</b> | 0.0% touches: <b>{n_000}</b> | "
            f"POC touches: <b>{n_poc}</b> | errors: {errors}")

# ══════════════════════════════════════════════════════════════════════════════
# universe.py — Full-NSE symbol list + MCAP/PRICE filters
# ══════════════════════════════════════════════════════════════════════════════
# Filters (your confirmed spec):
#   • Market-cap  > ₹1,000 crore  (= ₹10,00,00,00,000 = 1e10 INR)
#   • Last price >= ₹100
#
# Strategy:
#   1. Load seed list (nse_symbols.txt — full-NSE snapshot, no .NS suffix).
#   2. Optionally refresh from live sources (NSE India / GitHub mirrors).
#   3. PRICE pre-filter via one cheap batch download.
#   4. MCAP filter via threaded Yahoo quotes with a 7-day disk cache
#      (market-cap barely moves day to day — no need to re-pull for 2000 tickers).
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("fibo.universe")

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
MCAP_CACHE = CACHE_DIR / "mcap_cache.json"

MCAP_MIN_CRORE = 1000.0
MCAP_MIN_INR = MCAP_MIN_CRORE * 1e7  # 1 cr = 1e7 INR → 1000cr = 1e10
PRICE_MIN = 100.0

# Best-effort live sources for the full equity list (tried in order).
NSE_LIST_URLS = [
    # NSE official: full equity list
    "https://archives.nseindia.com/content/equity/EQUITY_L.csv",
    # Nifty 500 (top ~500 by free-float mcap — covers nearly all >1000cr names)
    "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
]
MIRROR_LIST_URLS = [
    "https://raw.githubusercontent.com/prateek1992/nse-stock-list/master/NSE_Equity_List.csv",
    "https://raw.githubusercontent.com/nse-tools/nse-tools/main/docs/symbols.txt",
]


def load_seed_symbols(path: Optional[Path] = None) -> List[str]:
    p = Path(path) if path else ROOT / "nse_symbols.txt"
    syms: List[str] = []
    if not p.exists():
        log.warning("seed list %s missing", p)
        return syms
    for line in p.read_text().splitlines():
        s = line.strip().upper().replace(".NS", "")
        if s and not s.startswith("#") and s != "SYMBOL":
            syms.append(s)
    # de-dup, keep order
    return list(dict.fromkeys(syms))


def to_yahoo(sym: str) -> str:
    s = sym.strip().upper()
    return s if s.endswith(".NS") else s + ".NS"


def refresh_full_nse_list(timeout: int = 25) -> List[str]:
    """Try to download the live full-NSE equity list. Returns [] on failure."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) fibo-scanner",
               "Accept": "text/csv,*/*"}
    for url in NSE_LIST_URLS + MIRROR_LIST_URLS:
        try:
            log.info("trying NSE list: %s", url)
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code != 200 or len(r.content) < 500:
                continue
            text = r.content.decode("utf-8", errors="ignore")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if len(lines) < 50:
                continue
            header = lines[0].upper()
            # CSV with SYMBOL column?
            if "SYMBOL" in header:
                import csv, io
                rows = list(csv.DictReader(io.StringIO(text)))
                key = next((k for k in rows[0].keys() if k and k.strip().upper() == "SYMBOL"), None)
                if key:
                    syms = [row[key].strip().upper().replace(".NS", "")
                            for row in rows if row.get(key, "").strip()]
                    syms = [s for s in syms if s and s != "SYMBOL"]
                    if len(syms) > 100:
                        log.info("got %d symbols from %s", len(syms), url)
                        return list(dict.fromkeys(syms))
            else:
                # plain symbol-per-line
                syms = [l.split(",")[0].strip().upper().replace(".NS", "") for l in lines]
                syms = [s for s in syms if s.isalnum() or "-" in s or "&" in s]
                if len(syms) > 100:
                    return list(dict.fromkeys(syms))
        except Exception as e:  # noqa: BLE001
            log.debug("NSE list %s failed: %s", url, e)
            continue
    log.warning("all live NSE-list sources failed — using seed file")
    return []


# ── market-cap cache ──────────────────────────────────────────────────────────

def _load_mcap_cache() -> Dict[str, dict]:
    try:
        if MCAP_CACHE.exists():
            return json.loads(MCAP_CACHE.read_text())
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_mcap_cache(cache: Dict[str, dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        MCAP_CACHE.write_text(json.dumps(cache))
    except Exception as e:  # noqa: BLE001
        log.debug("mcap cache save failed: %s", e)


def fetch_market_caps(symbols_ns: List[str], max_workers: int = 8,
                      ttl_days: int = 7) -> Dict[str, Optional[float]]:
    """{symbol.NS: marketCap INR | None} with disk cache."""
    import yfinance as yf
    cache = _load_mcap_cache()
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    out: Dict[str, Optional[float]] = {}
    todo: List[str] = []
    for s in symbols_ns:
        hit = cache.get(s)
        if hit and hit.get("ts"):
            try:
                if datetime.fromisoformat(hit["ts"]) > cutoff and hit.get("mcap"):
                    out[s] = float(hit["mcap"])
                    continue
            except Exception:  # noqa: BLE001
                pass
        todo.append(s)

    log.info("market-cap: %d cached, fetching %d…", len(out), len(todo))

    def _one(sym: str):
        for attempt in range(2):
            try:
                info = yf.Ticker(sym).info or {}
                mc = info.get("marketCap") or info.get("market_cap")
                return sym, (float(mc) if mc else None)
            except Exception as e:  # noqa: BLE001
                if attempt == 0:
                    time.sleep(1.0)
                    continue
                log.debug("%s: mcap failed: %s", sym, e)
                return sym, None
        return sym, None

    if todo:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_one, s) for s in todo]
            for i, f in enumerate(as_completed(futs), 1):
                sym, mc = f.result()
                out[sym] = mc
                if mc:
                    cache[sym] = {"mcap": mc,
                                  "ts": datetime.now(timezone.utc).isoformat()}
                if i % 100 == 0:
                    log.info("mcap progress %d/%d…", i, len(todo))
        _save_mcap_cache(cache)
    return out


# ── main entry ────────────────────────────────────────────────────────────────

def build_universe(min_price: float = PRICE_MIN,
                   min_mcap_cr: float = MCAP_MIN_CRORE,
                   refresh_list: bool = True,
                   seed_path: Optional[Path] = None,
                   max_workers: int = 4,
                   limit: Optional[int] = None) -> List[str]:
    """
    Full pipeline → sorted list of Yahoo tickers (with .NS) passing filters.
    Set limit=N for quick smoke tests.
    """
    from datafeed import fetch_last_closes  # local import (same folder)

    seeds = load_seed_symbols(seed_path)
    log.info("seed list: %d symbols", len(seeds))
    if refresh_list:
        live = refresh_full_nse_list()
        if live:
            seeds = list(dict.fromkeys(live + seeds))
            log.info("universe after live refresh: %d symbols", len(seeds))

    ysyms = [to_yahoo(s) for s in seeds]
    if limit:
        ysyms = ysyms[:limit]

    # 1) PRICE filter (cheap batch)
    closes = fetch_last_closes(ysyms, max_workers=max_workers)
    pass_price = [s for s in ysyms
                  if closes.get(s) is not None and closes[s] >= min_price]
    log.info("price ≥ ₹%g: %d/%d pass", min_price, len(pass_price), len(ysyms))

    # 2) MCAP filter (cached quotes)
    if min_mcap_cr and min_mcap_cr > 0:
        caps = fetch_market_caps(pass_price, max_workers=8)
        need = min_mcap_cr * 1e7
        final = [s for s in pass_price if caps.get(s) and caps[s] >= need]
        log.info("mcap > ₹%g cr: %d/%d pass", min_mcap_cr, len(final), len(pass_price))
    else:
        final = pass_price

    # persist for transparency
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        (CACHE_DIR / "last_universe.json").write_text(json.dumps({
            "asof": datetime.now(timezone.utc).isoformat(),
            "min_price": min_price, "min_mcap_cr": min_mcap_cr,
            "count": len(final), "symbols": sorted(final),
        }, indent=1))
    except Exception:  # noqa: BLE001
        pass
    return sorted(final)

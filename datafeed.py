# ══════════════════════════════════════════════════════════════════════════════
# datafeed.py — Yahoo Finance data layer (daily history + live intraday partials)
# ══════════════════════════════════════════════════════════════════════════════
# Design for full-NSE scale:
#   • Chunked batch downloads (yf.download with N tickers per call, not 1 call
#     per ticker) to survive Yahoo rate limits.
#   • Closed daily history is cached in memory + refreshed slowly (levels on the
#     DAILY timeframe only change on a new daily bar / live partial bar).
#   • Live "today partial" daily bar is aggregated from 1-minute bars each poll,
#     so the engine sees exactly what TradingView's realtime daily bar shows.
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

log = logging.getLogger("fibo.datafeed")

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None


REQUIRED_COLS = ["open", "high", "low", "close", "volume"]

_CHUNK_DAILY = 120   # tickers per yf.download call (daily)
_CHUNK_1M = 60       # tickers per yf.download call (1m — heavier)


def _require_yf():
    if yf is None:
        raise RuntimeError("yfinance is not installed. Run: pip install -r requirements.txt")


def _normalize_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Return clean single-symbol OHLCV frame with lowercase cols, tz-naive index."""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=REQUIRED_COLS)
    if isinstance(df.columns, pd.MultiIndex):
        # yfinance multi-ticker shape: (Price, Ticker) — pick our ticker
        try:
            if symbol in df.columns.get_level_values(1):
                df = df.xs(symbol, axis=1, level=1)
            else:  # fallback: first ticker block
                df = df.xs(df.columns.get_level_values(1)[0], axis=1, level=1)
        except Exception:
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.copy()
    df.columns = [str(c).strip().lower().replace(" ", "") for c in df.columns]
    # yfinance sometimes returns 'adjclose'; prefer it only if 'close' missing
    if "close" not in df.columns and "adjclose" in df.columns:
        df["close"] = df["adjclose"]
    for c in REQUIRED_COLS:
        if c not in df.columns:
            df[c] = float("nan")
    df = df[REQUIRED_COLS]
    for c in REQUIRED_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.index = pd.to_datetime(df.index)
    try:
        df.index = df.index.tz_convert(None)
    except Exception:
        try:
            df.index = df.index.tz_localize(None)
        except Exception:
            pass
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # drop fully-empty bars (holidays rows Yahoo sometimes ships)
    df = df.dropna(subset=["open", "high", "low", "close"], how="all")
    return df


def _download_chunk(tickers: List[str], period: str, interval: str,
                    retries: int = 3, delay: float = 2.0):
    _require_yf()
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            df = yf.download(
                tickers=" ".join(tickers) if len(tickers) > 1 else tickers[0],
                period=period, interval=interval,
                auto_adjust=False, progress=False, threads=True,
                timeout=30,
            )
            return df
        except Exception as e:  # noqa: BLE001 — transient network/Yahoo errors
            last_err = e
            log.warning("download %s %s failed (try %d/%d): %s",
                        period, interval, attempt + 1, retries, e)
            time.sleep(delay * (attempt + 1))
    log.error("download %s %s gave up for %d tickers: %s", period, interval,
              len(tickers), last_err)
    return None


def fetch_daily_batch(symbols_ns: List[str], period: str = "5y",
                       max_workers: int = 4) -> Dict[str, pd.DataFrame]:
    """
    Fetch daily OHLCV for many NSE symbols (.NS suffix). Returns {symbol: df}.
    Symbols that fail come back as empty frames (caller skips + logs).
    """
    out: Dict[str, pd.DataFrame] = {s: pd.DataFrame(columns=REQUIRED_COLS)
                                    for s in symbols_ns}
    if not symbols_ns:
        return out
    chunks = [symbols_ns[i:i + _CHUNK_DAILY]
              for i in range(0, len(symbols_ns), _CHUNK_DAILY)]
    log.info("fetching DAILY %s for %d symbols in %d chunk(s)…",
             period, len(symbols_ns), len(chunks))

    def _one(chunk: List[str]):
        return chunk, _download_chunk(chunk, period, "1d")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, c) for c in chunks]
        for f in as_completed(futs):
            chunk, raw = f.result()
            if raw is None or len(raw) == 0:
                continue
            for sym in chunk:
                try:
                    out[sym] = _normalize_ohlcv(raw, sym)
                except Exception as e:  # noqa: BLE001
                    log.warning("%s: normalize failed: %s", sym, e)
    ok = sum(1 for v in out.values() if len(v) > 0)
    log.info("daily fetch done: %d/%d symbols OK", ok, len(symbols_ns))
    return out


def fetch_today_partials(symbols_ns: List[str],
                         max_workers: int = 4) -> Dict[str, dict]:
    """
    Build TODAY's developing daily bar per symbol from 1-minute data.
    Returns {symbol: {date, open, high, low, close, volume} | None}.
    Falls back to Yahoo fast quote (flat bar) when 1m data is unavailable
    (e.g. market closed / pre-open) so callers always get *something*.
    """
    _require_yf()
    out: Dict[str, dict] = {s: None for s in symbols_ns}
    if not symbols_ns:
        return out
    chunks = [symbols_ns[i:i + _CHUNK_1M]
              for i in range(0, len(symbols_ns), _CHUNK_1M)]

    def _one(chunk: List[str]):
        return chunk, _download_chunk(chunk, "1d", "1m", retries=2, delay=1.5)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_one, c) for c in chunks]
        for f in as_completed(futs):
            chunk, raw = f.result()
            if raw is None or len(raw) == 0:
                continue
            for sym in chunk:
                try:
                    m = _normalize_ohlcv(raw, sym).dropna(
                        subset=["open", "high", "low", "close"], how="any")
                    if len(m) == 0:
                        continue
                    day = m.index[-1].date()
                    md = m[m.index.date == day]
                    if len(md) == 0:
                        md = m
                    out[sym] = {
                        "date": pd.Timestamp(day),
                        "open": float(md["open"].iloc[0]),
                        "high": float(md["high"].max()),
                        "low": float(md["low"].min()),
                        "close": float(md["close"].iloc[-1]),
                        "volume": float(pd.to_numeric(md["volume"],
                                                     errors="coerce").fillna(0).sum()),
                    }
                except Exception as e:  # noqa: BLE001
                    log.debug("%s: 1m partial failed: %s", sym, e)

    # Fallback for missing symbols: fast quote → flat bar (touch == exact hit)
    missing = [s for s, v in out.items() if v is None]
    if missing:
        log.info("%d symbols missing 1m data — trying fast quotes…", len(missing))

        def _quote(sym: str):
            try:
                t = yf.Ticker(sym)
                fi = t.fast_info
                px = float(fi.last_price)
                dh = float(getattr(fi, "day_high", px) or px)
                dl = float(getattr(fi, "day_low", px) or px)
                return sym, {"date": pd.Timestamp(datetime.now().date()),
                             "open": px, "high": max(px, dh), "low": min(px, dl),
                             "close": px, "volume": 0.0}
            except Exception as e:  # noqa: BLE001
                log.debug("%s: fast quote failed: %s", sym, e)
                return sym, None

        with ThreadPoolExecutor(max_workers=8) as ex:
            for sym, bar in ex.map(_quote, missing):
                out[sym] = bar
    n_ok = sum(1 for v in out.values() if v is not None)
    log.info("today-partials done: %d/%d symbols OK", n_ok, len(symbols_ns))
    return out


def merge_partial(daily: pd.DataFrame, partial: Optional[dict]) -> pd.DataFrame:
    """
    Append (or REPLACE same-date row of) today's partial bar onto closed daily
    history — so the engine evaluates exactly TradingView's realtime daily bar.
    """
    if daily is None or len(daily) == 0 or not partial:
        return daily
    d = pd.Timestamp(partial["date"])
    row = pd.DataFrame([{"open": partial["open"], "high": partial["high"],
                         "low": partial["low"], "close": partial["close"],
                         "volume": partial["volume"]}], index=[d])
    if len(daily) and pd.Timestamp(daily.index[-1]).date() == d.date():
        merged = pd.concat([daily.iloc[:-1], row])
    else:
        merged = pd.concat([daily, row])
    merged.index = pd.to_datetime(merged.index)
    return merged.sort_index()


def fetch_last_closes(symbols_ns: List[str],
                      max_workers: int = 4) -> Dict[str, Optional[float]]:
    """Cheap last-close map (5d daily) used for the PRICE >= 100 pre-filter."""
    closes: Dict[str, Optional[float]] = {}
    data = fetch_daily_batch(symbols_ns, period="5d", max_workers=max_workers)
    for sym, df in data.items():
        try:
            closes[sym] = float(df["close"].dropna().iloc[-1]) if len(df) else None
        except Exception:  # noqa: BLE001
            closes[sym] = None
    return closes

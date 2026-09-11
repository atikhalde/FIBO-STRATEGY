# ══════════════════════════════════════════════════════════════════════════════
# engine.py — EXACT Python port of "Swing Fibonacci Arcs & Volume Profile [BigBeluga]"
# ══════════════════════════════════════════════════════════════════════════════
# Pine source: "Swing Fibonacci Arcs & Volume Profile.txt" (Pine v6, swingLength=70)
#
# What this port covers (the parts that matter for alerts):
#   1. Swing detection ........ highest(high,70) / lowest(low,70), direction flips
#   2. 0.0% level .............. = swing anchor price (Lv on bull leg, Hv on bear leg)
#   3. Volume Profile + POC .... triangular-weighted close-volume distribution
#                                 from swing anchor bar → last bar, POC = argmax bin
#   4. Touch .................... low <= level <= high  (exact wick touch, no tolerance)
#
# Fidelity notes (read before "optimizing"):
#   • Bar-by-bar loop replicates Pine `var` semantics, including the order
#     "high-check first, low-check second" (low wins ties) and flip logic that
#     uses PREVIOUS-bar Hi/Hv/Li/Lv values.
#   • ATR = Wilder's RMA(TR,100)*0.3 with SMA seed (na before 100 bars), exactly
#     like ta.atr(100). ATR only affects VP row count; nz() fallback included.
#   • VP row count = int(min(max(span/(atr*0.5),10),100)) with truncation,
#     weight = max(0, 1-dist/(rowHeight*2)), POC = FIRST argmax (strict > loop).
#   • Arc geometry (xScale/yScale/minDx/minDy) only affects arc CURVATURE, never
#     the 0.0% price or POC price, so it is intentionally not part of alerts.
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ── Pine inputs ───────────────────────────────────────────────────────────────
SWING_LENGTH = 70
ATR_LEN = 100
ATR_MULT = 0.3


@dataclass
class PineLevels:
    """Levels on the LAST bar of the frame — pixel-equivalent to TradingView."""
    n_bars: int
    last_date: Optional[pd.Timestamp]

    # Swing state (final bar)
    direction_bull: bool          # True = bullish leg (anchor is a swing LOW)
    swing_price: float            # ← 0.0% LEVEL (the anchor price)
    swing_index: int              # position of anchor bar in frame (0-based)
    swing_date: Optional[pd.Timestamp]
    change_x: int
    change_y: float

    # Volume profile / POC
    poc_price: Optional[float]    # None when VP undefined (same as Pine: no line)
    poc_vol: Optional[float]
    max_vol: Optional[float]
    vp_rows: Optional[int]
    vp_pmin: Optional[float]
    vp_pmax: Optional[float]
    atr_last: Optional[float]

    # Diagnostics
    symbol: str = ""
    history: dict = field(default_factory=dict)  # optional per-bar arrays


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                length: int = ATR_LEN) -> np.ndarray:
    """ta.atr(length): RMA of True Range with SMA seed, na before `length` bars."""
    n = len(close)
    tr = np.empty(n, dtype=float)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i - 1]),
                    abs(low[i] - close[i - 1]))
    rma = np.full(n, np.nan, dtype=float)
    if n >= length:
        rma[length - 1] = float(np.mean(tr[:length]))
        for i in range(length, n):
            rma[i] = (rma[i - 1] * (length - 1) + tr[i]) / length
    return rma


def _rolling_extremes(high: np.ndarray, low: np.ndarray,
                      length: int = SWING_LENGTH):
    """ta.highest/ta.lowest with min_periods=1 (uses available bars at the start)."""
    h = pd.Series(high).rolling(length, min_periods=1).max().to_numpy()
    l = pd.Series(low).rolling(length, min_periods=1).min().to_numpy()
    return h, l


def compute_pine_levels(df: pd.DataFrame,
                        symbol: str = "",
                        swing_length: int = SWING_LENGTH,
                        atr_len: int = ATR_LEN,
                        keep_history: bool = False) -> PineLevels:
    """
    Run the EXACT Pine swing+VPS engine over an OHLCV frame.

    df columns required: open, high, low, close, volume (lowercase).
    Index should be datetime (used for dates only, never for math).
    Returns levels evaluated on the LAST row (== Pine `barstate.islast`).
    """
    if df is None or len(df) < 2:
        raise ValueError("need at least 2 bars")

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    vol_raw = df["volume"].to_numpy(dtype=float)
    # Pine nz(volume, 1.0) — only NaN becomes 1.0, zeros stay zero.
    volume = np.where(np.isnan(vol_raw), 1.0, vol_raw)

    n = len(df)
    H = _rolling_extremes(high, low, swing_length)[0]
    L = _rolling_extremes(high, low, swing_length)[1]
    atr = _wilder_atr(high, low, close, atr_len) * ATR_MULT

    # ── Pine `var` initial state (values as of the FIRST bar) ─────────────────
    Hi: int = 0
    Hv: float = float(high[0])
    Li: int = 0
    Lv: float = float(low[0])
    direction: bool = False

    swing_price: float = float("nan")
    swing_index: int = -1  # -1 == na
    locked_x1: int = -1
    locked_y1: float = float("nan")
    locked_x2: int = -1
    locked_y2: float = float("nan")

    change_y: float = float(high[0])
    change_x: int = 0

    dir_hist = np.zeros(n, dtype=bool) if keep_history else None
    sp_hist = np.full(n, np.nan) if keep_history else None
    si_hist = np.full(n, -1) if keep_history else None

    # ── Main bar loop — statement order mirrors the Pine source ───────────────
    for i in range(n):
        h70 = H[i]
        l70 = L[i]

        prev_direction = direction
        prev_Hi, prev_Hv = Hi, Hv
        prev_Li, prev_Lv = Li, Lv

        # NOTE: high first, low second — a bar that is BOTH keeps direction=false.
        if high[i] == h70:
            Hi = i
            Hv = float(high[i])
            direction = True
        if low[i] == l70:
            Li = i
            Lv = float(low[i])
            direction = False

        # direction != direction[1] — on bar 0 direction[1] is na → no flip.
        if i > 0 and direction != prev_direction:
            if direction:
                # flipped BULLISH: anchor = last swing low
                change_y = float(high[i])
                swing_price = float(Lv)
                swing_index = int(Li)
                locked_x1, locked_y1 = int(Li), float(Lv)
                locked_x2, locked_y2 = int(prev_Hi), float(prev_Hv)
            else:
                # flipped BEARISH: anchor = last swing high
                change_y = float(low[i])
                swing_index = int(Hi)
                swing_price = float(Hv)
                locked_x1, locked_y1 = int(Hi), float(Hv)
                locked_x2, locked_y2 = int(prev_Li), float(prev_Lv)

        # Real-time leg tracking (runs AFTER the flip block, same bar — Pine order)
        if (not direction) and low[i] == l70 and low[i] <= change_y:
            change_y = float(low[i])
            change_x = i
        if direction and high[i] == h70 and high[i] >= change_y:
            change_y = float(high[i])
            change_x = i

        if keep_history:
            dir_hist[i] = direction
            sp_hist[i] = swing_price
            si_hist[i] = swing_index

    last = n - 1
    idx = df.index
    last_date = pd.Timestamp(idx[last]) if len(idx) else None
    swing_date = pd.Timestamp(idx[swing_index]) if swing_index >= 0 else None

    # ── Volume Profile + POC (drawVolumeProfile on islast) ────────────────────
    poc_price: Optional[float] = None
    poc_vol: Optional[float] = None
    max_vol: Optional[float] = None
    vp_rows: Optional[int] = None
    vp_pmin: Optional[float] = None
    vp_pmax: Optional[float] = None
    atr_last = None if (not np.isfinite(atr[last])) else float(atr[last])

    if swing_index >= 0:
        start_bar = min(swing_index, last)
        end_bar = last
        if end_bar > start_bar:
            pmin = float(np.min(low[start_bar:end_bar + 1]))
            pmax = float(np.max(high[start_bar:end_bar + 1]))
            if pmax > pmin:
                current_atr = float(atr[last]) if np.isfinite(atr[last]) else (pmax - pmin) / 50.0
                price_span = pmax - pmin
                raw_rows = price_span / (current_atr * 0.5) if current_atr > 0 else 100.0
                # Pine int() truncates toward zero (= floor for positives)
                calc_rows = int(min(max(raw_rows, 10.0), 100.0))
                row_h = price_span / calc_rows

                seg_close = close[start_bar:end_bar + 1]
                seg_vol = volume[start_bar:end_bar + 1]
                mids = pmin + (np.arange(calc_rows, dtype=float) + 0.5) * row_h
                window = row_h * 2.0

                # Vectorized form of the Pine double loop:
                #   dist=|close-mid|; if dist<=window: w=max(0,1-dist/window); bin+=vol*w
                dist = np.abs(seg_close[:, None] - mids[None, :])
                w = 1.0 - dist / window
                w[dist > window] = 0.0
                np.maximum(w, 0.0, out=w)
                bins = (seg_vol[:, None] * w).sum(axis=0)

                mv = float(np.max(bins))
                if mv > 0:
                    poc_idx = int(np.argmax(bins))  # first max == Pine strict-> loop
                    poc_price = float(pmin + (poc_idx + 0.5) * row_h)
                    poc_vol = float(bins[poc_idx])
                    max_vol = mv
                    vp_rows = calc_rows
                    vp_pmin, vp_pmax = pmin, pmax

    levels = PineLevels(
        n_bars=n, last_date=last_date,
        direction_bull=bool(direction),
        swing_price=float(swing_price), swing_index=int(swing_index),
        swing_date=swing_date, change_x=int(change_x), change_y=float(change_y),
        poc_price=poc_price, poc_vol=poc_vol, max_vol=max_vol,
        vp_rows=vp_rows, vp_pmin=vp_pmin, vp_pmax=vp_pmax, atr_last=atr_last,
        symbol=symbol,
    )
    if keep_history:
        levels.history = {"direction": dir_hist, "swing_price": sp_hist,
                          "swing_index": si_hist}
    return levels


def is_touch(bar_high: float, bar_low: float, level: Optional[float]) -> bool:
    """EXACT indicator touch: the bar's wick contains the level. No tolerance."""
    if level is None or (isinstance(level, float) and (math.isnan(level) or math.isinf(level))):
        return False
    if bar_high is None or bar_low is None:
        return False
    if (isinstance(bar_high, float) and (math.isnan(bar_high) or math.isinf(bar_high))) or \
       (isinstance(bar_low, float) and (math.isnan(bar_low) or math.isinf(bar_low))):
        return False
    return bool(bar_low <= level <= bar_high)


def _anchor_of(lv: PineLevels) -> Optional[float]:
    if lv.swing_index < 0 or not np.isfinite(lv.swing_price):
        return None
    return float(lv.swing_price)


def scan_last_bar(df: pd.DataFrame, symbol: str = "",
                  swing_length: int = SWING_LENGTH) -> dict:
    """
    Compute levels on the last bar and evaluate the 2 alert conditions there.
    Returns a dict ready for formatting / alerting.

    PRIOR-BAR ANCHOR RULE (Alert 1): the 0.0% level is a trailing extreme, so a
    bar that retraces back to it ALWAYS flips the swing on that same bar (its
    low/high becomes the new 70-bar extreme) — the anchor then jumps to the
    opposite extreme and a naive "islast anchor" check can never fire. The
    tradable event is the touch of the anchor as it stood at the PREVIOUS bar
    close (the level drawn on your chart before the market opened). Hence:
        touch_000 = bar touches current anchor OR previous-bar anchor.
    Level COMPUTATION itself is untouched and pixel-identical to TradingView.
    """
    lv = compute_pine_levels(df, symbol=symbol, swing_length=swing_length)
    bh = float(df["high"].iloc[-1])
    bl = float(df["low"].iloc[-1])
    bc = float(df["close"].iloc[-1])

    lvl000 = _anchor_of(lv)

    # Anchor as it stood on the previous bar (yesterday's drawn 0.0% level).
    lvl000_prev: Optional[float] = None
    swing_date_prev = None
    if len(df) >= 3:
        try:
            lv_prev = compute_pine_levels(df.iloc[:-1], swing_length=swing_length)
            lvl000_prev = _anchor_of(lv_prev)
            swing_date_prev = lv_prev.swing_date
        except ValueError:
            pass

    touch_cur = is_touch(bh, bl, lvl000)
    touch_prev = is_touch(bh, bl, lvl000_prev) if lvl000_prev is not None else False
    # If both anchors coincide it is a single level — report as current.
    if touch_cur and touch_prev and lvl000 == lvl000_prev:
        touch_prev = False
    touch000 = touch_cur or touch_prev
    touch_poc = is_touch(bh, bl, lv.poc_price)

    def _dist_pct(price: float, level: Optional[float]) -> Optional[float]:
        if level is None or level == 0 or price is None:
            return None
        return (price - level) / abs(level) * 100.0

    return {
        "symbol": symbol,
        "last_date": lv.last_date,
        "close": bc, "high": bh, "low": bl,
        "direction": "BULL-LEG (anchor=swing LOW)" if lv.direction_bull else "BEAR-LEG (anchor=swing HIGH)",
        "direction_bull": lv.direction_bull,
        "level_000": lvl000,
        "swing_date": lv.swing_date,
        "level_000_prev": lvl000_prev,
        "swing_date_prev": swing_date_prev,
        "touched_000": ("current" if touch_cur else ("prev-bar" if touch_prev else None)),
        "poc": lv.poc_price,
        "poc_vol": lv.poc_vol,
        "vp_rows": lv.vp_rows,
        "vp_pmin": lv.vp_pmin, "vp_pmax": lv.vp_pmax,
        "atr": lv.atr_last,
        "n_bars": lv.n_bars,
        "touch_000": touch000,
        "touch_poc": touch_poc,
        "dist_000_pct": _dist_pct(bc, lvl000),
        "dist_poc_pct": _dist_pct(bc, lv.poc_price),
    }


def historical_touches(df: pd.DataFrame, symbol: str = "",
                       swing_length: int = SWING_LENGTH,
                       start: Optional[int] = None) -> pd.DataFrame:
    """
    Backtest-style: for every bar j, compute levels as Pine would show with
    `islast==j`, and record whether bar j touches them. Causal (prefix slices).
    O(n²)-ish — use on a few hundred bars for verification/backtests only.
    """
    n = len(df)
    j0 = 1 if start is None else max(1, start)  # bar 0 alone can never hold a swing
    rows = []
    prev_anchor: Optional[float] = None
    for j in range(j0, n):
        sub = df.iloc[:j + 1]
        try:
            lv = compute_pine_levels(sub, symbol=symbol, swing_length=swing_length)
        except ValueError:
            continue
        bh = float(sub["high"].iloc[-1])
        bl = float(sub["low"].iloc[-1])
        lvl000 = _anchor_of(lv)
        # Prior-bar anchor rule (see scan_last_bar): touch counts against the
        # anchor that was drawn BEFORE bar j, as well as the post-bar anchor.
        t000 = is_touch(bh, bl, lvl000) or is_touch(bh, bl, prev_anchor)
        rows.append({
            "date": sub.index[-1],
            "close": float(sub["close"].iloc[-1]),
            "swing_price": lvl000,
            "swing_price_prev": prev_anchor,
            "swing_date": lv.swing_date,
            "poc": lv.poc_price,
            "touch_000": t000,
            "touch_poc": is_touch(bh, bl, lv.poc_price),
        })
        prev_anchor = lvl000
    return pd.DataFrame(rows)

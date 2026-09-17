# ══════════════════════════════════════════════════════════════════════════════
# backtest_engine.py — Core historical backtest scanner & performance analyzer
# ══════════════════════════════════════════════════════════════════════════════
# Implements causal historical scanning for:
#   1. 0.0% touches (swing anchor support/resistance tests, including prior-bar)
#   2. POC touches (Volume Profile Point of Control tests)
#   3. Backtest periods: 1yr, 2yr, 3yr, 5yr
#   4. Comprehensive stock performance metrics (trade simulation, win rate,
#      profit factor, forward returns +1d/+3d/+5d/+10d/+20d, MFE, MAE, max DD)
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine import (ATR_LEN, ATR_MULT, SWING_LENGTH, _anchor_of,
                    _rolling_extremes, _wilder_atr, is_touch)

log = logging.getLogger("fibo.backtest")


@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    entry_price: float
    touch_type: str            # '0.0% Level' or 'POC Level'
    level_price: float
    swing_direction: str       # 'BULL' or 'BEAR'
    trade_direction: str       # 'LONG' or 'SHORT'
    exit_date: str
    exit_price: float
    outcome: str               # 'TARGET', 'STOP_LOSS', 'TIME_EXIT'
    return_pct: float
    holding_days: int
    ret_1d: Optional[float] = None
    ret_3d: Optional[float] = None
    ret_5d: Optional[float] = None
    ret_10d: Optional[float] = None
    ret_20d: Optional[float] = None
    mfe_pct: Optional[float] = None  # Max Favorable Excursion %
    mae_pct: Optional[float] = None  # Max Adverse Excursion %


@dataclass
class StockPerformance:
    symbol: str
    total_touches: int
    touches_000: int
    touches_poc: int
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    avg_return_pct: float
    total_return_pct: float
    profit_factor: float
    best_trade_pct: float
    worst_trade_pct: float
    avg_mfe_pct: float
    avg_mae_pct: float


@dataclass
class BacktestResult:
    # Metadata & Parameters
    period: str                     # '1y', '2y', '3y', '5y'
    period_label: str
    touch_filter: str               # '0.0%', 'POC', 'Both'
    universe_name: str
    start_date: str
    end_date: str
    total_symbols_scanned: int
    symbols_with_touches: int

    # Trade Simulation Strategy Parameters
    target_pct: float
    stop_pct: float
    max_hold_days: int

    # Overall Trade Metrics
    total_touches: int
    touches_000: int
    touches_poc: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    loss_rate_pct: float
    profit_factor: float
    total_strategy_return_pct: float
    avg_trade_return_pct: float
    avg_winning_return_pct: float
    avg_losing_return_pct: float
    win_loss_ratio: float
    max_drawdown_pct: float
    avg_holding_days: float

    # Outcome Breakdown
    target_hit_count: int
    target_hit_rate_pct: float
    stop_loss_hit_count: int
    stop_loss_hit_rate_pct: float
    time_exit_count: int
    time_exit_rate_pct: float

    # Extremes
    best_trade: dict
    worst_trade: dict

    # Forward Horizon Statistics
    horizon_stats: Dict[str, dict]

    # Touch Type Breakdown
    touch_type_stats: Dict[str, dict]

    # Stock-by-stock rankings
    stock_performance: List[StockPerformance]

    # Time series & Trade details
    equity_curve: List[dict]
    trade_log: List[TradeRecord]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def fast_scan_symbol_history(df: pd.DataFrame, symbol: str = "",
                             swing_length: int = SWING_LENGTH,
                             atr_len: int = ATR_LEN,
                             atr_mult: float = ATR_MULT) -> pd.DataFrame:
    """
    Lightning-fast causal scan of the entire history for a symbol.
    Exact mathematical equivalent to running Pine indicator bar-by-bar.
    Returns DataFrame indexed by date with columns:
      close, high, low, open, volume, swing_price, swing_date, direction_bull,
      poc, touch_000, touch_poc
    """
    if df is None or len(df) < 5:
        return pd.DataFrame()

    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    open_ = df["open"].to_numpy(dtype=float)
    vol_raw = df["volume"].to_numpy(dtype=float)
    volume = np.where(np.isnan(vol_raw), 1.0, vol_raw)

    n = len(df)
    H = _rolling_extremes(high, low, swing_length)[0]
    L = _rolling_extremes(high, low, swing_length)[1]
    atr = _wilder_atr(high, low, close, atr_len) * atr_mult

    Hi: int = 0
    Hv: float = float(high[0])
    Li: int = 0
    Lv: float = float(low[0])
    direction: bool = False

    swing_price: float = float("nan")
    swing_index: int = -1
    change_y: float = float(high[0])
    change_x: int = 0

    rows = []
    prev_anchor: Optional[float] = None
    prev_swing_date = None

    dates = pd.to_datetime(df.index)

    for i in range(n):
        h70 = H[i]
        l70 = L[i]
        prev_direction = direction
        prev_Hi, prev_Hv = Hi, Hv
        prev_Li, prev_Lv = Li, Lv

        if high[i] == h70:
            Hi = i
            Hv = float(high[i])
            direction = True
        if low[i] == l70:
            Li = i
            Lv = float(low[i])
            direction = False

        if i > 0 and direction != prev_direction:
            if direction:
                change_y = float(high[i])
                swing_price = float(Lv)
                swing_index = int(Li)
            else:
                change_y = float(low[i])
                swing_index = int(Hi)
                swing_price = float(Hv)

        if (not direction) and low[i] == l70 and low[i] <= change_y:
            change_y = float(low[i])
            change_x = i
        if direction and high[i] == h70 and high[i] >= change_y:
            change_y = float(high[i])
            change_x = i

        if i == 0:
            prev_anchor = swing_price if (swing_index >= 0 and np.isfinite(swing_price)) else None
            prev_swing_date = dates[swing_index] if swing_index >= 0 else None
            continue

        # Causal POC calculation on bar i
        poc_price = None
        if swing_index >= 0:
            start_bar = min(swing_index, i)
            end_bar = i
            if end_bar > start_bar:
                pmin = float(np.min(low[start_bar:end_bar + 1]))
                pmax = float(np.max(high[start_bar:end_bar + 1]))
                if pmax > pmin:
                    cur_atr = float(atr[i]) if np.isfinite(atr[i]) else (pmax - pmin) / 50.0
                    price_span = pmax - pmin
                    raw_rows = price_span / (cur_atr * 0.5) if cur_atr > 0 else 100.0
                    calc_rows = int(min(max(raw_rows, 10.0), 100.0))
                    row_h = price_span / calc_rows

                    seg_close = close[start_bar:end_bar + 1]
                    seg_vol = volume[start_bar:end_bar + 1]
                    mids = pmin + (np.arange(calc_rows, dtype=float) + 0.5) * row_h
                    window = row_h * 2.0

                    dist = np.abs(seg_close[:, None] - mids[None, :])
                    w = 1.0 - dist / window
                    w[dist > window] = 0.0
                    np.maximum(w, 0.0, out=w)
                    bins = (seg_vol[:, None] * w).sum(axis=0)

                    mv = float(np.max(bins))
                    if mv > 0:
                        poc_idx = int(np.argmax(bins))
                        poc_price = float(pmin + (poc_idx + 0.5) * row_h)

        lvl000 = swing_price if (swing_index >= 0 and np.isfinite(swing_price)) else None
        bh = float(high[i])
        bl = float(low[i])
        touch_cur = is_touch(bh, bl, lvl000)
        touch_prev = is_touch(bh, bl, prev_anchor) if prev_anchor is not None else False
        touch000 = touch_cur or touch_prev
        touch_poc = is_touch(bh, bl, poc_price)

        s_date = dates[swing_index] if swing_index >= 0 else None

        rows.append({
            "date": dates[i],
            "open": float(open_[i]),
            "high": bh,
            "low": bl,
            "close": float(close[i]),
            "volume": float(volume[i]),
            "direction_bull": bool(direction),
            "swing_price": lvl000,
            "swing_price_prev": prev_anchor,
            "swing_date": s_date,
            "poc": poc_price,
            "touch_000": touch000,
            "touch_poc": touch_poc,
            "touch_000_type": "current" if touch_cur else ("prev-bar" if touch_prev else None),
        })

        prev_anchor = lvl000
        prev_swing_date = s_date

    res_df = pd.DataFrame(rows)
    if not res_df.empty:
        res_df.set_index("date", inplace=True)
    return res_df


def parse_period_cutoff(df_index: pd.DatetimeIndex, period: str) -> Tuple[pd.Timestamp, pd.Timestamp]:
    """Calculate the start and end dates for 1y, 2y, 3y, 5y periods."""
    period_clean = period.strip().lower().replace("yr", "y")
    years_map = {"1y": 1, "2y": 2, "3y": 3, "5y": 5}
    years = years_map.get(period_clean, 1)

    max_date = df_index[-1]
    start_date = max_date - pd.DateOffset(years=years)
    return pd.Timestamp(start_date), pd.Timestamp(max_date)


def simulate_stock_touches(scan_df: pd.DataFrame, symbol: str,
                           touch_filter: str = "both",
                           start_date: Optional[pd.Timestamp] = None,
                           end_date: Optional[pd.Timestamp] = None,
                           target_pct: float = 4.0,
                           stop_pct: float = 2.0,
                           max_hold_days: int = 10,
                           cooldown_bars: int = 3) -> Tuple[List[TradeRecord], List[dict]]:
    """
    Simulate trades and calculate forward performance for all qualifying touches.
    """
    trades: List[TradeRecord] = []
    all_touch_events: List[dict] = []

    if scan_df.empty or len(scan_df) < 5:
        return trades, all_touch_events

    filter_norm = touch_filter.strip().lower()
    dates = scan_df.index
    n = len(scan_df)

    last_trade_bar = -999

    for i in range(n):
        bar_date = dates[i]
        if start_date is not None and bar_date < start_date:
            continue
        if end_date is not None and bar_date > end_date:
            continue

        row = scan_df.iloc[i]
        t000 = bool(row["touch_000"])
        tpoc = bool(row["touch_poc"])

        fired_000 = False
        fired_poc = False

        if filter_norm in ("0.0%", "0.0", "zero", "0.0% touches"):
            if not t000:
                continue
            fired_000 = True
        elif filter_norm in ("poc", "poc touches"):
            if not tpoc:
                continue
            fired_poc = True
        else:  # 'both', 'all'
            if not (t000 or tpoc):
                continue
            fired_000 = t000
            fired_poc = tpoc

        # Determine touch label and reference level price
        if fired_000 and fired_poc:
            touch_type_label = "0.0% & POC"
            level_px = row["swing_price"] if pd.notna(row["swing_price"]) else row["poc"]
        elif fired_000:
            touch_type_label = "0.0% Level"
            level_px = row["swing_price"] if pd.notna(row["swing_price"]) else row["swing_price_prev"]
        else:
            touch_type_label = "POC Level"
            level_px = row["poc"]

        swing_dir = "BULL" if row["direction_bull"] else "BEAR"
        # In Bull leg: anchor is swing LOW, touch is support pullback -> LONG
        # In Bear leg: anchor is swing HIGH, touch is resistance rally -> SHORT
        trade_dir = "LONG" if row["direction_bull"] else "SHORT"

        entry_price = float(row["close"])
        entry_date_str = str(bar_date.date())

        # Forward horizons calculations (1d, 3d, 5d, 10d, 20d)
        def _get_fwd_ret(k: int) -> Optional[float]:
            if i + k < n:
                fwd_c = float(scan_df["close"].iloc[i + k])
                raw_ret = (fwd_c - entry_price) / entry_price * 100.0
                return raw_ret if trade_dir == "LONG" else -raw_ret
            return None

        ret_1d = _get_fwd_ret(1)
        ret_3d = _get_fwd_ret(3)
        ret_5d = _get_fwd_ret(5)
        ret_10d = _get_fwd_ret(10)
        ret_20d = _get_fwd_ret(20)

        # MFE and MAE over max_hold_days window
        window_end = min(n, i + max_hold_days + 1)
        if window_end > i + 1:
            fw_highs = scan_df["high"].iloc[i + 1:window_end].to_numpy()
            fw_lows = scan_df["low"].iloc[i + 1:window_end].to_numpy()
            if trade_dir == "LONG":
                mfe_pct = float(np.max((fw_highs - entry_price) / entry_price * 100.0))
                mae_pct = float(np.min((fw_lows - entry_price) / entry_price * 100.0))
            else:
                mfe_pct = float(np.max((entry_price - fw_lows) / entry_price * 100.0))
                mae_pct = float(np.min((entry_price - fw_highs) / entry_price * 100.0))
        else:
            mfe_pct = 0.0
            mae_pct = 0.0

        event_data = {
            "symbol": symbol,
            "date": entry_date_str,
            "touch_type": touch_type_label,
            "level_price": float(level_px) if pd.notna(level_px) else entry_price,
            "close": entry_price,
            "swing_dir": swing_dir,
            "trade_dir": trade_dir,
            "ret_1d": ret_1d,
            "ret_5d": ret_5d,
            "ret_10d": ret_10d,
            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,
        }
        all_touch_events.append(event_data)

        # Position simulation with cooldown
        if i - last_trade_bar < cooldown_bars:
            continue

        # Simulate trade execution
        exit_date_str = entry_date_str
        exit_price = entry_price
        outcome = "TIME_EXIT"
        holding_days = 0

        target_mult = 1.0 + target_pct / 100.0 if trade_dir == "LONG" else 1.0 - target_pct / 100.0
        stop_mult = 1.0 - stop_pct / 100.0 if trade_dir == "LONG" else 1.0 + stop_pct / 100.0

        target_price = entry_price * target_mult
        stop_price = entry_price * stop_mult

        trade_resolved = False
        for h in range(1, max_hold_days + 1):
            if i + h >= n:
                # Reached end of data
                exit_price = float(scan_df["close"].iloc[-1])
                exit_date_str = str(dates[-1].date())
                holding_days = h - 1
                outcome = "TIME_EXIT"
                trade_resolved = True
                break

            h_high = float(scan_df["high"].iloc[i + h])
            h_low = float(scan_df["low"].iloc[i + h])
            h_close = float(scan_df["close"].iloc[i + h])
            h_date = str(dates[i + h].date())

            if trade_dir == "LONG":
                # Conservative: check stop loss first
                if h_low <= stop_price:
                    outcome = "STOP_LOSS"
                    exit_price = stop_price
                    exit_date_str = h_date
                    holding_days = h
                    trade_resolved = True
                    break
                elif h_high >= target_price:
                    outcome = "TARGET"
                    exit_price = target_price
                    exit_date_str = h_date
                    holding_days = h
                    trade_resolved = True
                    break
            else:  # SHORT
                if h_high >= stop_price:
                    outcome = "STOP_LOSS"
                    exit_price = stop_price
                    exit_date_str = h_date
                    holding_days = h
                    trade_resolved = True
                    break
                elif h_low <= target_price:
                    outcome = "TARGET"
                    exit_price = target_price
                    exit_date_str = h_date
                    holding_days = h
                    trade_resolved = True
                    break

            if h == max_hold_days:
                # Time exit at close of day H
                outcome = "TIME_EXIT"
                exit_price = h_close
                exit_date_str = h_date
                holding_days = h
                trade_resolved = True
                break

        if not trade_resolved:
            exit_price = entry_price
            holding_days = 0

        # Calculate trade return %
        if trade_dir == "LONG":
            trade_ret = (exit_price - entry_price) / entry_price * 100.0
        else:
            trade_ret = (entry_price - exit_price) / entry_price * 100.0

        rec = TradeRecord(
            symbol=symbol,
            entry_date=entry_date_str,
            entry_price=round(entry_price, 2),
            touch_type=touch_type_label,
            level_price=round(float(level_px) if pd.notna(level_px) else entry_price, 2),
            swing_direction=swing_dir,
            trade_direction=trade_dir,
            exit_date=exit_date_str,
            exit_price=round(exit_price, 2),
            outcome=outcome,
            return_pct=round(trade_ret, 2),
            holding_days=holding_days,
            ret_1d=round(ret_1d, 2) if ret_1d is not None else None,
            ret_3d=round(ret_3d, 2) if ret_3d is not None else None,
            ret_5d=round(ret_5d, 2) if ret_5d is not None else None,
            ret_10d=round(ret_10d, 2) if ret_10d is not None else None,
            ret_20d=round(ret_20d, 2) if ret_20d is not None else None,
            mfe_pct=round(mfe_pct, 2) if mfe_pct is not None else None,
            mae_pct=round(mae_pct, 2) if mae_pct is not None else None,
        )
        trades.append(rec)
        last_trade_bar = i

    return trades, all_touch_events


def run_historical_backtest(symbols_data: Dict[str, pd.DataFrame],
                            touch_filter: str = "both",
                            period: str = "1y",
                            universe_name: str = "Full NSE Universe",
                            target_pct: float = 4.0,
                            stop_pct: float = 2.0,
                            max_hold_days: int = 10,
                            cooldown_bars: int = 3) -> BacktestResult:
    """
    Run complete historical backtest scanner across multiple stocks.
    Aggregates performance metrics, horizon stats, equity curve, and rankings.
    """
    period_norm = period.strip().lower().replace("yr", "y")
    period_labels = {
        "1y": "1 Year",
        "2y": "2 Years",
        "3y": "3 Years",
        "5y": "5 Years",
    }
    period_label = period_labels.get(period_norm, f"{period} Period")

    touch_norm = touch_filter.strip().lower()
    if touch_norm in ("0.0", "0.0%", "0.0% touches"):
        touch_title = "0.0% Touches (Swing Anchor)"
    elif touch_norm in ("poc", "poc touches"):
        touch_title = "POC Touches (Point of Control)"
    else:
        touch_title = "Both (0.0% & POC Touches)"

    # Determine global date bounds
    all_dates = []
    for df in symbols_data.values():
        if df is not None and not df.empty:
            all_dates.extend(df.index)
    if not all_dates:
        raise ValueError("No data available to backtest.")

    global_max_date = pd.to_datetime(all_dates).max()
    years_map = {"1y": 1, "2y": 2, "3y": 3, "5y": 5}
    years = years_map.get(period_norm, 1)
    start_date = global_max_date - pd.DateOffset(years=years)
    end_date = global_max_date

    all_trades: List[TradeRecord] = []
    stock_metrics_list: List[StockPerformance] = []
    symbol_scan_dfs: Dict[str, pd.DataFrame] = {}

    total_touches_000 = 0
    total_touches_poc = 0

    for sym, df in symbols_data.items():
        if df is None or len(df) < 50:
            continue
        try:
            scan_df = fast_scan_symbol_history(df, symbol=sym)
            if scan_df.empty:
                continue
            symbol_scan_dfs[sym] = scan_df

            trades, events = simulate_stock_touches(
                scan_df, symbol=sym,
                touch_filter=touch_filter,
                start_date=start_date,
                end_date=end_date,
                target_pct=target_pct,
                stop_pct=stop_pct,
                max_hold_days=max_hold_days,
                cooldown_bars=cooldown_bars,
            )

            # Count touches within period
            period_sub = scan_df[(scan_df.index >= start_date) & (scan_df.index <= end_date)]
            n_000 = int(period_sub["touch_000"].sum())
            n_poc = int(period_sub["touch_poc"].sum())
            total_touches_000 += n_000
            total_touches_poc += n_poc
            tot_t = len(events)

            if trades:
                wins = sum(1 for t in trades if t.return_pct > 0)
                losses = sum(1 for t in trades if t.return_pct < 0)
                tot_trades = len(trades)
                wr = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
                rets = [t.return_pct for t in trades]
                avg_ret = float(np.mean(rets))
                tot_ret = float(np.sum(rets))
                gains = sum(r for r in rets if r > 0)
                loss_sum = abs(sum(r for r in rets if r < 0))
                pf = (gains / loss_sum) if loss_sum > 0 else (99.9 if gains > 0 else 1.0)
                best_t = max(rets)
                worst_t = min(rets)
                mfes = [t.mfe_pct for t in trades if t.mfe_pct is not None]
                maes = [t.mae_pct for t in trades if t.mae_pct is not None]
                avg_mfe = float(np.mean(mfes)) if mfes else 0.0
                avg_mae = float(np.mean(maes)) if maes else 0.0
            else:
                wins = losses = tot_trades = 0
                wr = avg_ret = tot_ret = 0.0
                pf = 1.0
                best_t = worst_t = avg_mfe = avg_mae = 0.0

            stock_metrics_list.append(StockPerformance(
                symbol=sym,
                total_touches=tot_t,
                touches_000=n_000,
                touches_poc=n_poc,
                total_trades=len(trades),
                wins=wins,
                losses=losses,
                win_rate_pct=round(wr, 1),
                avg_return_pct=round(avg_ret, 2),
                total_return_pct=round(tot_ret, 2),
                profit_factor=round(min(pf, 99.9), 2),
                best_trade_pct=round(best_t, 2),
                worst_trade_pct=round(worst_t, 2),
                avg_mfe_pct=round(avg_mfe, 2),
                avg_mae_pct=round(avg_mae, 2),
            ))
            all_trades.extend(trades)

        except Exception as e:
            log.warning("Backtest failed for %s: %s", sym, e)
            continue

    # Sort trades chronologically
    all_trades.sort(key=lambda t: (t.entry_date, t.symbol))

    # Overall Metrics Calculation
    tot_trades = len(all_trades)
    winning_trades = sum(1 for t in all_trades if t.return_pct > 0)
    losing_trades = sum(1 for t in all_trades if t.return_pct < 0)
    be_trades = tot_trades - winning_trades - losing_trades
    win_rate = (winning_trades / tot_trades * 100.0) if tot_trades > 0 else 0.0
    loss_rate = (losing_trades / tot_trades * 100.0) if tot_trades > 0 else 0.0

    all_returns = [t.return_pct for t in all_trades]
    gross_profits = sum(r for r in all_returns if r > 0)
    gross_losses = abs(sum(r for r in all_returns if r < 0))
    profit_factor = (gross_profits / gross_losses) if gross_losses > 0 else (99.9 if gross_profits > 0 else 1.0)

    avg_trade_return = float(np.mean(all_returns)) if all_returns else 0.0
    tot_strategy_return = float(np.sum(all_returns)) if all_returns else 0.0

    winning_returns = [r for r in all_returns if r > 0]
    losing_returns = [r for r in all_returns if r < 0]
    avg_win = float(np.mean(winning_returns)) if winning_returns else 0.0
    avg_loss = float(np.mean(losing_returns)) if losing_returns else 0.0
    win_loss_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else (avg_win if avg_win > 0 else 1.0)

    # Outcomes
    target_hits = sum(1 for t in all_trades if t.outcome == "TARGET")
    stop_hits = sum(1 for t in all_trades if t.outcome == "STOP_LOSS")
    time_exits = sum(1 for t in all_trades if t.outcome == "TIME_EXIT")
    target_hit_rate = (target_hits / tot_trades * 100.0) if tot_trades > 0 else 0.0
    stop_hit_rate = (stop_hits / tot_trades * 100.0) if tot_trades > 0 else 0.0
    time_exit_rate = (time_exits / tot_trades * 100.0) if tot_trades > 0 else 0.0

    holding_days_list = [t.holding_days for t in all_trades]
    avg_holding_days = float(np.mean(holding_days_list)) if holding_days_list else 0.0

    # Best & Worst Trades
    if all_trades:
        best_t = max(all_trades, key=lambda t: t.return_pct)
        worst_t = min(all_trades, key=lambda t: t.return_pct)
        best_dict = {"symbol": best_t.symbol, "date": best_t.entry_date, "return_pct": best_t.return_pct}
        worst_dict = {"symbol": worst_t.symbol, "date": worst_t.entry_date, "return_pct": worst_t.return_pct}
    else:
        best_dict = {"symbol": "N/A", "date": "N/A", "return_pct": 0.0}
        worst_dict = {"symbol": "N/A", "date": "N/A", "return_pct": 0.0}

    # Forward Horizon Statistics
    horizon_stats: Dict[str, dict] = {}
    for h_label, attr in [("1-Day", "ret_1d"), ("3-Day", "ret_3d"),
                          ("5-Day", "ret_5d"), ("10-Day", "ret_10d"),
                          ("20-Day", "ret_20d")]:
        vals = [getattr(t, attr) for t in all_trades if getattr(t, attr) is not None]
        if vals:
            wins_h = sum(1 for v in vals if v > 0)
            wr_h = wins_h / len(vals) * 100.0
            horizon_stats[h_label] = {
                "count": len(vals),
                "win_rate_pct": round(wr_h, 1),
                "avg_return_pct": round(float(np.mean(vals)), 2),
                "median_return_pct": round(float(np.median(vals)), 2),
                "std_pct": round(float(np.std(vals)), 2),
                "best_pct": round(float(np.max(vals)), 2),
                "worst_pct": round(float(np.min(vals)), 2),
            }
        else:
            horizon_stats[h_label] = {
                "count": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0,
                "median_return_pct": 0.0, "std_pct": 0.0, "best_pct": 0.0, "worst_pct": 0.0
            }

    # Touch Type Breakdown
    touch_type_stats: Dict[str, dict] = {}
    for t_type in ["0.0% Level", "POC Level"]:
        sub_trades = [t for t in all_trades if t_type in t.touch_type]
        if sub_trades:
            sub_wins = sum(1 for t in sub_trades if t.return_pct > 0)
            sub_rets = [t.return_pct for t in sub_trades]
            sub_gains = sum(r for r in sub_rets if r > 0)
            sub_losses = abs(sum(r for r in sub_rets if r < 0))
            sub_pf = (sub_gains / sub_losses) if sub_losses > 0 else (99.9 if sub_gains > 0 else 1.0)
            sub_mfes = [t.mfe_pct for t in sub_trades if t.mfe_pct is not None]
            sub_maes = [t.mae_pct for t in sub_trades if t.mae_pct is not None]
            touch_type_stats[t_type] = {
                "trades": len(sub_trades),
                "win_rate_pct": round(sub_wins / len(sub_trades) * 100.0, 1),
                "avg_return_pct": round(float(np.mean(sub_rets)), 2),
                "profit_factor": round(min(sub_pf, 99.9), 2),
                "avg_mfe_pct": round(float(np.mean(sub_mfes)), 2) if sub_mfes else 0.0,
                "avg_mae_pct": round(float(np.mean(sub_maes)), 2) if sub_maes else 0.0,
            }
        else:
            touch_type_stats[t_type] = {
                "trades": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0,
                "profit_factor": 1.0, "avg_mfe_pct": 0.0, "avg_mae_pct": 0.0
            }

    # Equity Curve & Maximum Drawdown
    equity_curve: List[dict] = []
    cur_equity = 100.0
    peak_equity = 100.0
    max_dd = 0.0

    if all_trades:
        # Sort trades by exit date for equity compounding
        chron_trades = sorted(all_trades, key=lambda t: t.exit_date)
        for t in chron_trades:
            # 2% fixed fractional risk or 1x return
            cur_equity *= (1.0 + t.return_pct / 100.0)
            if cur_equity > peak_equity:
                peak_equity = cur_equity
            dd = (peak_equity - cur_equity) / peak_equity * 100.0
            if dd > max_dd:
                max_dd = dd
            equity_curve.append({
                "date": t.exit_date,
                "strategy_equity": round(cur_equity, 2),
                "trade_return": t.return_pct,
            })
    else:
        equity_curve.append({"date": str(start_date.date()), "strategy_equity": 100.0, "trade_return": 0.0})

    # Sort stock performance ranking by total return desc
    stock_metrics_list.sort(key=lambda s: (s.total_return_pct, s.win_rate_pct), reverse=True)

    syms_with_t = sum(1 for s in stock_metrics_list if s.total_touches > 0)

    # Filtered touch count based on user selection
    if touch_norm in ("0.0", "0.0%", "0.0% touches"):
        active_total_touches = total_touches_000
    elif touch_norm in ("poc", "poc touches"):
        active_total_touches = total_touches_poc
    else:
        active_total_touches = total_touches_000 + total_touches_poc

    return BacktestResult(
        period=period_norm,
        period_label=period_label,
        touch_filter=touch_title,
        universe_name=universe_name,
        start_date=str(start_date.date()),
        end_date=str(end_date.date()),
        total_symbols_scanned=len(symbols_data),
        symbols_with_touches=syms_with_t,
        target_pct=target_pct,
        stop_pct=stop_pct,
        max_hold_days=max_hold_days,
        total_touches=active_total_touches,
        touches_000=total_touches_000,
        touches_poc=total_touches_poc,
        total_trades=tot_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        breakeven_trades=be_trades,
        win_rate_pct=round(win_rate, 1),
        loss_rate_pct=round(loss_rate, 1),
        profit_factor=round(min(profit_factor, 99.9), 2),
        total_strategy_return_pct=round(tot_strategy_return, 2),
        avg_trade_return_pct=round(avg_trade_return, 2),
        avg_winning_return_pct=round(avg_win, 2),
        avg_losing_return_pct=round(avg_loss, 2),
        win_loss_ratio=round(win_loss_ratio, 2),
        max_drawdown_pct=round(max_dd, 1),
        avg_holding_days=round(avg_holding_days, 1),
        target_hit_count=target_hits,
        target_hit_rate_pct=round(target_hit_rate, 1),
        stop_loss_hit_count=stop_hits,
        stop_loss_hit_rate_pct=round(stop_hit_rate, 1),
        time_exit_count=time_exits,
        time_exit_rate_pct=round(time_exit_rate, 1),
        best_trade=best_dict,
        worst_trade=worst_dict,
        horizon_stats=horizon_stats,
        touch_type_stats=touch_type_stats,
        stock_performance=stock_metrics_list,
        equity_curve=equity_curve,
        trade_log=all_trades,
    )

#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════════════════════
# test_backtest.py — Unit and integration tests for historical backtest scanner
# ══════════════════════════════════════════════════════════════════════════════

import unittest
from pathlib import Path
import pandas as pd
import numpy as np

from backtest_engine import (
    fast_scan_symbol_history,
    simulate_stock_touches,
    run_historical_backtest,
    BacktestResult,
)
from data_manager import get_universe_symbols, generate_realistic_stock_data, load_batch_history
from pdf_report import build_pdf_report


class TestFiboBacktest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Generate 2 synthetic stock series with 1400 bars (5+ years)
        cls.df_rel = generate_realistic_stock_data("RELIANCE", n_bars=1400)
        cls.df_tcs = generate_realistic_stock_data("TCS", n_bars=1400)
        cls.test_data = {"RELIANCE": cls.df_rel, "TCS": cls.df_tcs}

    def test_fast_scan_equivalence_and_columns(self):
        """Verify that fast_scan_symbol_history returns valid Pine indicator columns
        including anchor-aware bull flags (100% parity with engine.scan_last_bar)."""
        scan_df = fast_scan_symbol_history(self.df_rel, symbol="RELIANCE")
        self.assertFalse(scan_df.empty)
        required_cols = [
            "open", "high", "low", "close", "volume",
            "direction_bull", "prev_direction_bull",
            "swing_price", "swing_price_prev",
            "poc", "touch_000", "touch_000_cur", "touch_000_prev",
            "touch_000_bull", "touch_poc", "touch_poc_bull", "touch_any_bull"
        ]
        for col in required_cols:
            self.assertIn(col, scan_df.columns, f"Missing required column: {col}")

    def test_touch_options(self):
        """Verify backtest supports 0.0%, POC, and Both touch options."""
        # 1) 0.0% touches only
        res_000 = run_historical_backtest(self.test_data, touch_filter="0.0%", period="2y")
        self.assertEqual(res_000.touch_filter, "0.0% Touches (Swing Anchor, Bull-side LONG)")
        self.assertEqual(res_000.total_touches, res_000.touches_000)

        # 2) POC touches only
        res_poc = run_historical_backtest(self.test_data, touch_filter="poc", period="2y")
        self.assertEqual(res_poc.touch_filter, "POC Touches (Point of Control, Bull-side LONG)")
        self.assertEqual(res_poc.total_touches, res_poc.touches_poc)

        # 3) Both touches
        res_both = run_historical_backtest(self.test_data, touch_filter="both", period="2y")
        self.assertEqual(res_both.touch_filter, "Both (0.0% & POC Touches, Bull-side LONG)")
        self.assertEqual(res_both.total_touches, res_both.touches_000 + res_both.touches_poc)

    def test_bull_side_only(self):
        """LIVE-PARITY (anchor-aware): only BULL-anchor signals — every trade LONG
        on a bull swing LOW (or POC support on a bull leg). 0.0% bull touches
        are anchor-aware (prev vs current), because every 0.0% touch flips the
        leg on the same bar; filtering on post-flip direction_bull alone would
        misclassify 80%+ of true bull touches. Touch counts cover anchor-aware
        bull rows only, 100% parity with live scanner."""
        res = run_historical_backtest(self.test_data, touch_filter="both", period="2y")

        # 1) Every simulated trade is a LONG trade on a BULL anchor / bull leg
        self.assertGreater(len(res.trade_log), 0)
        for t in res.trade_log:
            self.assertEqual(t.trade_direction, "LONG",
                             f"SHORT trade leaked into backtest: {t}")
            self.assertEqual(t.swing_direction, "BULL",
                             f"non-bull-leg trade leaked into backtest: {t}")

        # 2) Every trade's entry bar really is an anchor-aware bull touch bar
        start = max(df.index.max() for df in self.test_data.values()) - pd.DateOffset(years=2)
        for t in res.trade_log:
            scan_df = fast_scan_symbol_history(self.test_data[t.symbol], symbol=t.symbol)
            row = scan_df.loc[pd.Timestamp(t.entry_date)]
            # Anchor-aware: 0.0% bull = (cur touch & cur bull) or (prev touch & prev bull)
            is_bull_touch = bool(row["touch_000_bull"]) or bool(row["touch_poc_bull"])
            self.assertTrue(is_bull_touch,
                            f"{t.symbol} {t.entry_date}: entry bar is not an anchor-aware bull touch "
                            f"(touch_000_bull={row['touch_000_bull']}, touch_poc_bull={row['touch_poc_bull']})")
            # Also verify raw touch exists
            self.assertTrue(bool(row["touch_000"]) or bool(row["touch_poc"]),
                            f"{t.symbol} {t.entry_date}: entry bar has no raw touch")

        # 3) Reported touch counts equal anchor-aware bull touch rows in window
        exp_000 = exp_poc = 0
        for df in self.test_data.values():
            scan_df = fast_scan_symbol_history(df)
            mask = scan_df.index >= start
            exp_000 += int(scan_df.loc[mask, "touch_000_bull"].sum())
            exp_poc += int(scan_df.loc[mask, "touch_poc_bull"].sum())
        self.assertEqual(res.touches_000, exp_000,
                         f"touches_000 mismatch: reported {res.touches_000} vs anchor-aware {exp_000}")
        self.assertEqual(res.touches_poc, exp_poc,
                         f"touches_poc mismatch: reported {res.touches_poc} vs anchor-aware {exp_poc}")

        # 4) Cross-check with engine.scan_last_bar's bull flags (0 mismatches)
        from engine import scan_last_bar
        for sym, df in self.test_data.items():
            scan_df = fast_scan_symbol_history(df, symbol=sym)
            for date, row in scan_df[scan_df.index >= start].iterrows():
                sub = df.loc[:date]
                eng = scan_last_bar(sub, symbol=sym)
                self.assertEqual(bool(row["touch_000_bull"]), bool(eng["touch_000_bull"]),
                                 f"{sym} {date.date()}: touch_000_bull mismatch fast={row['touch_000_bull']} eng={eng['touch_000_bull']}")
                self.assertEqual(bool(row["touch_poc_bull"]), bool(eng["touch_poc_bull"]),
                                 f"{sym} {date.date()}: touch_poc_bull mismatch")

    def test_no_cooldown_consecutive_touches(self):
        """LIVE-PARITY: no trade cooldown — consecutive-day touches each
        produce a trade, exactly like the live scanner re-arming daily."""
        n = 30
        idx = pd.bdate_range("2025-01-01", periods=n)
        base = np.full(n, 100.0)
        touch = np.zeros(n, dtype=bool)
        touch[10] = touch[11] = True   # two consecutive touch days
        # Anchor-aware bull flags: direction_bull True for all, so all touches are bull
        scan_df = pd.DataFrame({
            "open": base, "high": base + 5.0, "low": base - 5.0,
            "close": base + np.linspace(0, 1.0, n),  # drift up → target hits
            "volume": base,
            "direction_bull": np.ones(n, dtype=bool),
            "prev_direction_bull": np.ones(n, dtype=bool),
            "swing_price": base, "swing_price_prev": base,
            "swing_date": idx, "poc": base,
            "touch_000": touch,
            "touch_000_cur": touch, "touch_000_prev": np.zeros(n, dtype=bool),
            "touch_000_bull": touch, "touch_poc": np.zeros(n, dtype=bool),
            "touch_poc_bull": np.zeros(n, dtype=bool), "touch_any_bull": touch,
        }, index=idx)

        trades, events = simulate_stock_touches(scan_df, "SYNTH",
                                                touch_filter="0.0%",
                                                target_pct=4.0, stop_pct=2.0,
                                                max_hold_days=5)
        self.assertEqual(len(events), 2, "both consecutive touches must count")
        self.assertEqual(len(trades), 2,
                         "cooldown must be off: consecutive-day touches each trade")
        self.assertEqual([t.entry_date for t in trades],
                         [str(idx[10].date()), str(idx[11].date())])

    def test_period_options(self):
        """Verify backtest supports 1yr, 2yr, 3yr, and 5yr periods."""
        for p in ["1y", "2y", "3y", "5y"]:
            res = run_historical_backtest(self.test_data, touch_filter="both", period=p)
            self.assertEqual(res.period, p)
            self.assertIn("Year", res.period_label)
            self.assertGreater(len(res.equity_curve), 0)
            self.assertTrue(hasattr(res, "win_rate_pct"))
            self.assertTrue(hasattr(res, "profit_factor"))

    def test_pdf_report_generation(self):
        """Verify that a valid, non-empty PDF report is compiled successfully."""
        res = run_historical_backtest(self.test_data, touch_filter="both", period="1y")
        out_pdf = Path("reports/test_unit_report.pdf")
        if out_pdf.exists():
            out_pdf.unlink()
        
        pdf_path = build_pdf_report(res, out_pdf)
        self.assertTrue(pdf_path.exists())
        self.assertGreater(pdf_path.stat().st_size, 50_000, "PDF should be at least 50KB")
        out_pdf.unlink()

    def test_universe_selection(self):
        """Verify universe symbol parsing."""
        n50 = get_universe_symbols("nifty50")
        self.assertEqual(len(n50), 50)

        n100 = get_universe_symbols("nifty100")
        self.assertEqual(len(n100), 100)

        custom = get_universe_symbols("RELIANCE,TCS,INFY")
        self.assertEqual(custom, ["RELIANCE", "TCS", "INFY"])


if __name__ == "__main__":
    unittest.main()

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
        """Verify that fast_scan_symbol_history returns valid Pine indicator columns."""
        scan_df = fast_scan_symbol_history(self.df_rel, symbol="RELIANCE")
        self.assertFalse(scan_df.empty)
        required_cols = [
            "open", "high", "low", "close", "volume",
            "direction_bull", "swing_price", "swing_price_prev",
            "poc", "touch_000", "touch_poc"
        ]
        for col in required_cols:
            self.assertIn(col, scan_df.columns, f"Missing required column: {col}")

    def test_touch_options(self):
        """Verify backtest supports 0.0%, POC, and Both touch options."""
        # 1) 0.0% touches only
        res_000 = run_historical_backtest(self.test_data, touch_filter="0.0%", period="2y")
        self.assertEqual(res_000.touch_filter, "0.0% Touches (Swing Anchor)")
        self.assertEqual(res_000.total_touches, res_000.touches_000)

        # 2) POC touches only
        res_poc = run_historical_backtest(self.test_data, touch_filter="poc", period="2y")
        self.assertEqual(res_poc.touch_filter, "POC Touches (Point of Control)")
        self.assertEqual(res_poc.total_touches, res_poc.touches_poc)

        # 3) Both touches
        res_both = run_historical_backtest(self.test_data, touch_filter="both", period="2y")
        self.assertEqual(res_both.touch_filter, "Both (0.0% & POC Touches)")
        self.assertEqual(res_both.total_touches, res_both.touches_000 + res_both.touches_poc)

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

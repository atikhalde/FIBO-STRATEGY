#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════════════════════
# backtest_scanner.py — Historical Backtest Scanner (0.0% & POC Touches)
# ══════════════════════════════════════════════════════════════════════════════
# Allows scanning historical stocks performance with:
#   • 2 Touch options: 1) 0.0% touches  2) POC touches  (plus Both/All)
#   • Period selection: 1yr, 2yr, 3yr, 5yr
#   • Universe selection: Full NSE (>₹1,000 Cr Mcap), Nifty 50, Nifty 100, Custom
#   • Automated publication-quality PDF report generation
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest_engine import BacktestResult, run_historical_backtest
from data_manager import get_universe_symbols, load_batch_history
from pdf_report import build_pdf_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fibo.scanner_cli")


def format_table_header(title: str, width: int = 76) -> str:
    line = "═" * width
    return f"\n{line}\n  {title}\n{line}"


def print_cli_summary(res: BacktestResult, pdf_path: Optional[Path] = None):
    """Render terminal report with rich formatting."""
    print(format_table_header(f"FIBO HISTORICAL BACKTEST RESULT — {res.period_label.upper()} ({res.touch_filter.upper()})"))
    print(f"  Period:       {res.period_label} ({res.start_date} → {res.end_date})")
    print(f"  Touch Filter: {res.touch_filter}")
    print(f"  Universe:     {res.universe_name} ({res.total_symbols_scanned} symbols scanned, {res.symbols_with_touches} with touches)")
    print(f"  Strategy:     BULL-SIDE LONG ONLY (live-scanner rules) | Target: +{res.target_pct:.1f}% | Stop-Loss: -{res.stop_pct:.1f}% (1:2 R:R) | Max Hold: {res.max_hold_days} days")
    print("─" * 76)

    # Key Performance Metrics
    print(f"  {'TOTAL TOUCHES:':<22} {res.total_touches:<12} (0.0%: {res.touches_000} | POC: {res.touches_poc})")
    print(f"  {'TOTAL TRADES:':<22} {res.total_trades:<12} (Won: {res.winning_trades} | Lost: {res.losing_trades} | BE: {res.breakeven_trades})")
    print(f"  {'WIN RATE:':<22} {res.win_rate_pct:.1f}%")
    print(f"  {'PROFIT FACTOR:':<22} {res.profit_factor:.2f}x")
    print(f"  {'AVG TRADE RETURN:':<22} {res.avg_trade_return_pct:+.2f}% (Avg Win: {res.avg_winning_return_pct:+.2f}% | Avg Loss: {res.avg_losing_return_pct:+.2f}%)")
    print(f"  {'TOTAL STRATEGY RETURN:':<22} {res.total_strategy_return_pct:+.1f}%")
    print(f"  {'MAX DRAWDOWN:':<22} -{res.max_drawdown_pct:.1f}%")
    print(f"  {'AVG HOLDING PERIOD:':<22} {res.avg_holding_days:.1f} days")
    print(f"  {'OUTCOMES:':<22} Target: {res.target_hit_rate_pct:.1f}% | Stop Loss: {res.stop_loss_hit_rate_pct:.1f}% | Time Exit: {res.time_exit_rate_pct:.1f}%")
    print(f"  {'BEST TRADE:':<22} {res.best_trade['symbol']} ({res.best_trade['date']}) {res.best_trade['return_pct']:+.1f}%")
    print(f"  {'WORST TRADE:':<22} {res.worst_trade['symbol']} ({res.worst_trade['date']}) {res.worst_trade['return_pct']:+.1f}%")
    print("─" * 76)

    # Forward Horizon Statistics Table
    print(f"\n  FORWARD HORIZON PERFORMANCE (+1d, +3d, +5d, +10d, +20d after touch):")
    print(f"  {'Horizon':<10} {'Trades':<8} {'Win Rate':<12} {'Avg Return':<14} {'Median':<10} {'Max Gain':<10} {'Max Loss':<10}")
    print(f"  {'-'*10} {'-'*8} {'-'*12} {'-'*14} {'-'*10} {'-'*10} {'-'*10}")
    for h in ["1-Day", "3-Day", "5-Day", "10-Day", "20-Day"]:
        hd = res.horizon_stats.get(h, {})
        print(f"  {'+'+h:<10} {hd.get('count', 0):<8} {hd.get('win_rate_pct', 0.0):>5.1f}%     {hd.get('avg_return_pct', 0.0):>+7.2f}%       {hd.get('median_return_pct', 0.0):>+6.2f}%    {hd.get('best_pct', 0.0):>+6.2f}%    {hd.get('worst_pct', 0.0):>+6.2f}%")

    # Touch Type Breakdown
    print(f"\n  TOUCH TYPE COMPARISON (0.0% LEVEL vs POC LEVEL):")
    print(f"  {'Level Type':<28} {'Trades':<8} {'Win Rate':<12} {'Avg Return':<14} {'Profit Factor':<15} {'Avg MFE':<10}")
    print(f"  {'-'*28} {'-'*8} {'-'*12} {'-'*14} {'-'*15} {'-'*10}")
    for k in ["0.0% Level", "POC Level"]:
        st = res.touch_type_stats.get(k, {})
        print(f"  {k:<28} {st.get('trades', 0):<8} {st.get('win_rate_pct', 0.0):>5.1f}%     {st.get('avg_return_pct', 0.0):>+7.2f}%       {st.get('profit_factor', 0.0):>6.2f}x          {st.get('avg_mfe_pct', 0.0):>+5.2f}%")

    # Top Stocks Breakdown
    if res.stock_performance:
        print(f"\n  TOP PERFORMING STOCKS:")
        print(f"  {'Symbol':<14} {'Touches':<9} {'Trades':<8} {'Win Rate':<12} {'Avg Return':<14} {'Total Return':<14} {'Profit Factor':<12}")
        print(f"  {'-'*14} {'-'*9} {'-'*8} {'-'*12} {'-'*14} {'-'*14} {'-'*12}")
        for sp in res.stock_performance[:10]:
            print(f"  {sp.symbol:<14} {sp.total_touches:<9} {sp.total_trades:<8} {sp.win_rate_pct:>5.1f}%     {sp.avg_return_pct:>+7.2f}%       {sp.total_return_pct:>+7.1f}%       {sp.profit_factor:>6.2f}x")

    if pdf_path:
        print(f"\n" + "═" * 76)
        print(f"  📄 PDF REPORT GENERATED SUCCESSFULLY:")
        print(f"     Path: {pdf_path.resolve()}")
        print(f"     Size: {pdf_path.stat().st_size:,} bytes")
        print("═" * 76 + "\n")


def prompt_user_interactive() -> dict:
    """Friendly interactive CLI menu for selecting options."""
    print("\n" + "═" * 70)
    print("   FIBO STRATEGY — HISTORICAL BACKTEST SCANNER CONFIGURATION")
    print("═" * 70)

    # Touch type
    print("\n[1] Select Touch Type:")
    print("  1) 0.0% touches (Swing Anchor Level)")
    print("  2) POC touches (Point of Control Level)")
    print("  3) Both (0.0% & POC touches)")
    t_choice = input("Enter choice [1/2/3, default=3]: ").strip()
    touch_map = {"1": "0.0%", "2": "poc", "3": "both"}
    touch_filter = touch_map.get(t_choice, "both")

    # Period
    print("\n[2] Select Backtest Period:")
    print("  1) 1 Year  (1yr)")
    print("  2) 2 Years (2yr)")
    print("  3) 3 Years (3yr)")
    print("  4) 5 Years (5yr)")
    p_choice = input("Enter choice [1/2/3/4, default=2]: ").strip()
    period_map = {"1": "1y", "2": "2y", "3": "3y", "4": "5y"}
    period = period_map.get(p_choice, "2y")

    # Universe
    print("\n[3] Select Stock Universe:")
    print("  1) Nifty 50 (Top liquid large caps)")
    print("  2) Nifty 100")
    print("  3) Full Filtered NSE Universe (>₹1,000 Cr Mcap, Price ≥ ₹100)")
    print("  4) Custom comma-separated symbols")
    u_choice = input("Enter choice [1/2/3/4, default=1]: ").strip()
    if u_choice == "2":
        universe = "nifty100"
    elif u_choice == "3":
        universe = "full"
    elif u_choice == "4":
        custom_input = input("Enter symbols (e.g. RELIANCE,TCS,INFY): ").strip()
        universe = custom_input if custom_input else "nifty50"
    else:
        universe = "nifty50"

    return {
        "touch": touch_filter,
        "period": period,
        "universe": universe,
        "target_pct": 4.0,
        "stop_pct": 2.0,
        "max_hold": 10,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="FIBO Historical Backtest Scanner (0.0% & POC Touches)")
    parser.add_argument("--touch", choices=["0.0", "0.0%", "poc", "both", "all"], default=None,
                        help="Touch option: '0.0' (0.0%% touches), 'poc' (POC touches), or 'both'")
    parser.add_argument("--period", choices=["1y", "1yr", "2y", "2yr", "3y", "3yr", "5y", "5yr"], default=None,
                        help="Backtest duration: '1y', '2y', '3y', or '5y'")
    parser.add_argument("--universe", default="nifty50",
                        help="Universe basket: 'nifty50', 'nifty100', 'full' (all NSE >1000cr), or 'sample'")
    parser.add_argument("--symbols", default=None,
                        help="Specific comma-separated symbols to scan (e.g. RELIANCE,TCS,INFY)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of symbols to scan")
    parser.add_argument("--target-pct", type=float, default=4.0,
                        help="Profit target percentage (default: 4.0%%)")
    parser.add_argument("--stop-pct", type=float, default=2.0,
                        help="Stop loss percentage (default: 2.0%%)")
    parser.add_argument("--max-hold", type=int, default=10,
                        help="Maximum holding period in trading days (default: 10)")
    parser.add_argument("--pdf", default=None,
                        help="Output path for the generated PDF report")
    parser.add_argument("--no-pdf", action="store_true",
                        help="Skip generating PDF report")
    parser.add_argument("--workers", type=int, default=4,
                        help="Worker threads for data fetching")
    parser.add_argument("--interactive", action="store_true",
                        help="Prompt interactively for options")

    args = parser.parse_args(argv)

    # Check if interactive mode needed
    if args.interactive or (args.touch is None and args.period is None and len(sys.argv) == 1):
        inter_cfg = prompt_user_interactive()
        touch_option = inter_cfg["touch"]
        period_option = inter_cfg["period"]
        universe_option = inter_cfg["universe"]
        target_pct = inter_cfg["target_pct"]
        stop_pct = inter_cfg["stop_pct"]
        max_hold = inter_cfg["max_hold"]
        symbols_arg = None
    else:
        touch_option = args.touch or "both"
        period_option = args.period or "1y"
        universe_option = args.universe
        symbols_arg = args.symbols
        target_pct = args.target_pct
        stop_pct = args.stop_pct
        max_hold = args.max_hold

    # Standardize options
    period_norm = period_option.lower().replace("yr", "y")
    touch_norm = "0.0%" if touch_option in ("0.0", "0.0%") else ("POC" if touch_option.lower() == "poc" else "Both")

    # Resolve Universe Symbols
    if symbols_arg:
        symbols = [s.strip().upper().replace(".NS", "") for s in symbols_arg.split(",") if s.strip()]
        universe_label = f"Custom ({len(symbols)} symbols)"
    else:
        symbols = get_universe_symbols(universe_option, limit=args.limit)
        universe_labels = {
            "nifty50": "Nifty 50 Index Universe",
            "nifty100": "Nifty 100 Index Universe",
            "full": "Full NSE Filtered Universe (>₹1,000 Cr Mcap)",
            "sample": "Sample Universe (Top 20 Stocks)",
        }
        universe_label = universe_labels.get(universe_option.lower(), f"{universe_option} Universe")

    if not symbols:
        log.error("No symbols found for universe '%s'", universe_option)
        return 1

    log.info("Starting FIBO Historical Backtest...")
    log.info("Option 1: Touch filter = %s", touch_norm)
    log.info("Option 2: Period = %s (%s)", period_norm, {"1y": "1 Year", "2y": "2 Years", "3y": "3 Years", "5y": "5 Years"}.get(period_norm))
    log.info("Universe: %s (%d symbols)", universe_label, len(symbols))

    # LIVE-PARITY: the live scanner always runs the engine on 5y of daily
    # history (--history 5y default), so the backtest loads the same 5-year
    # context for every period choice. Trades/touches are still restricted to
    # the selected analysis window by run_historical_backtest.
    print(f"\n⚡ Loading historical data for {len(symbols)} symbols (5y context, live-parity)...")
    data = load_batch_history(symbols, period_years=5, max_workers=args.workers)
    if not data:
        log.error("Failed to load historical data for symbols.")
        return 1
    print(f"✅ Loaded {len(data)} stocks successfully.")

    print(f"⚡ Running causal Pine engine backtest ({touch_norm} touches, {period_norm})...")
    res = run_historical_backtest(
        symbols_data=data,
        touch_filter=touch_norm,
        period=period_norm,
        universe_name=universe_label,
        target_pct=target_pct,
        stop_pct=stop_pct,
        max_hold_days=max_hold,
    )

    # Determine PDF output path
    pdf_path = None
    if not args.no_pdf:
        if args.pdf:
            pdf_path = Path(args.pdf)
        else:
            clean_t = touch_norm.replace("%", "pct").replace(" ", "_").lower()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_path = ROOT / "reports" / f"fibo_backtest_{clean_t}_{period_norm}_{timestamp}.pdf"

        print(f"⚡ Generating institutional PDF report...")
        build_pdf_report(res, pdf_path)

    # Print summary to console
    print_cli_summary(res, pdf_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

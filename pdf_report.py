# ══════════════════════════════════════════════════════════════════════════════
# pdf_report.py — Institutional PDF Report Generator for FIBO Backtest
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (HRFlowable, Image, KeepTogether, PageBreak,
                                Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from backtest_engine import BacktestResult

log = logging.getLogger("fibo.pdf")

# Professional Color Palette
C_NAVY_DARK = colors.HexColor("#0F172A")
C_NAVY = colors.HexColor("#1E293B")
C_SLATE = colors.HexColor("#475569")
C_LIGHT_BG = colors.HexColor("#F8FAFC")
C_BORDER = colors.HexColor("#CBD5E1")
C_BLUE = colors.HexColor("#2563EB")
C_BLUE_LIGHT = colors.HexColor("#EFF6FF")
C_GREEN = colors.HexColor("#16A34A")
C_GREEN_LIGHT = colors.HexColor("#DCFCE7")
C_RED = colors.HexColor("#DC2626")
C_RED_LIGHT = colors.HexColor("#FEE2E2")
C_AMBER = colors.HexColor("#D97706")


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw total page count."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count: int):
        self.saveState()
        w, h = A4

        # Header running banner (pages 2+)
        if self._pageNumber > 1:
            self.setFont("Helvetica-Bold", 8)
            self.setFillColor(C_NAVY)
            self.drawString(36, h - 25, "FIBO-STRATEGY — HISTORICAL BACKTEST PERFORMANCE REPORT")
            self.setFont("Helvetica", 8)
            self.setFillColor(C_SLATE)
            self.drawRightString(w - 36, h - 25, "0.0% & POC Touches Scanner")
            self.setStrokeColor(C_BORDER)
            self.setLineWidth(0.5)
            self.line(36, h - 30, w - 36, h - 30)

        # Footer banner (all pages)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(C_SLATE)
        self.drawString(36, 20, "FIBO-STRATEGY — Swing Fibonacci Arcs & Volume Profile [BigBeluga] Exact Port")
        page_str = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(w - 36, 20, page_str)
        self.setStrokeColor(C_BORDER)
        self.setLineWidth(0.5)
        self.line(36, 30, w - 36, 30)

        self.restoreState()


def _generate_charts(res: BacktestResult) -> io.BytesIO:
    """Generate combined institutional charts image."""
    fig, axes = plt.subplots(2, 2, figsize=(10, 6.2), dpi=180)
    plt.subplots_adjust(hspace=0.38, wspace=0.28)

    # ── Chart 1: Equity Growth Curve ──
    ax1 = axes[0, 0]
    if res.equity_curve and len(res.equity_curve) > 1:
        eq_dates = [c["date"] for c in res.equity_curve]
        eq_vals = [c["strategy_equity"] for c in res.equity_curve]
        x_steps = np.arange(len(eq_vals))
        ax1.plot(x_steps, eq_vals, color="#2563EB", lw=2.0, label="Strategy Equity (Base 100)")
        ax1.fill_between(x_steps, 100, eq_vals, color="#3B82F6", alpha=0.15)
        ax1.axhline(100, color="#94A3B8", linestyle="--", lw=1.0)
        # Show sparse date ticks
        if len(eq_dates) > 5:
            indices = np.linspace(0, len(eq_dates) - 1, 5, dtype=int)
            ax1.set_xticks(indices)
            ax1.set_xticklabels([eq_dates[idx] for idx in indices], rotation=20, fontsize=7)
    else:
        ax1.text(0.5, 0.5, "Insufficient Trade Data", ha="center", va="center", color="#64748B")
    ax1.set_title("Cumulative Strategy Equity Curve", fontsize=9.5, fontweight="bold", color="#1E293B", pad=6)
    ax1.set_ylabel("Equity Index", fontsize=8, color="#475569")
    ax1.grid(True, linestyle=":", alpha=0.6)
    ax1.tick_params(labelsize=7.5)

    # ── Chart 2: Forward Return Horizons ──
    ax2 = axes[0, 1]
    horizons = ["1-Day", "3-Day", "5-Day", "10-Day", "20-Day"]
    win_rates = [res.horizon_stats.get(h, {}).get("win_rate_pct", 0.0) for h in horizons]
    avg_rets = [res.horizon_stats.get(h, {}).get("avg_return_pct", 0.0) for h in horizons]

    x = np.arange(len(horizons))
    bars = ax2.bar(x, avg_rets, width=0.45, color=["#16A34A" if r >= 0 else "#DC2626" for r in avg_rets], alpha=0.85)
    ax2.set_xticks(x)
    ax2.set_xticklabels(horizons, fontsize=7.5)
    ax2.axhline(0, color="#64748B", lw=0.8)
    for bar, wr in zip(bars, win_rates):
        h = bar.get_height()
        va = "bottom" if h >= 0 else "top"
        offset = 0.08 if h >= 0 else -0.15
        ax2.annotate(f"{h:+.1f}%\n({wr:.0f}% win)",
                     xy=(bar.get_x() + bar.get_width() / 2, h + offset),
                     xytext=(0, 0), textcoords="offset points",
                     ha="center", va=va, fontsize=6.8, fontweight="bold", color="#1E293B")
    ax2.set_title("Forward Performance by Horizon (+X Days)", fontsize=9.5, fontweight="bold", color="#1E293B", pad=6)
    ax2.set_ylabel("Avg Return %", fontsize=8, color="#475569")
    ax2.grid(True, linestyle=":", alpha=0.6, axis="y")
    ax2.tick_params(labelsize=7.5)

    # ── Chart 3: Return Distribution Histogram ──
    ax3 = axes[1, 0]
    if res.trade_log and len(res.trade_log) >= 3:
        rets = [t.return_pct for t in res.trade_log]
        n_bins = min(20, max(5, len(rets) // 3))
        counts, bin_edges, patches = ax3.hist(rets, bins=n_bins, edgecolor="#CBD5E1", lw=0.8)
        for edge, patch in zip(bin_edges, patches):
            if edge >= 0:
                patch.set_facecolor("#16A34A")
                patch.set_alpha(0.75)
            else:
                patch.set_facecolor("#DC2626")
                patch.set_alpha(0.75)
        ax3.axvline(0, color="#0F172A", linestyle="--", lw=1.2)
        ax3.axvline(res.avg_trade_return_pct, color="#2563EB", lw=1.5,
                    label=f"Avg: {res.avg_trade_return_pct:+.1f}%")
        ax3.legend(loc="upper right", fontsize=7.5, frameon=False)
    else:
        ax3.text(0.5, 0.5, "Few Trades Available", ha="center", va="center", color="#64748B")
    ax3.set_title("Trade Return Distribution", fontsize=9.5, fontweight="bold", color="#1E293B", pad=6)
    ax3.set_xlabel("Return %", fontsize=8, color="#475569")
    ax3.set_ylabel("Trade Count", fontsize=8, color="#475569")
    ax3.grid(True, linestyle=":", alpha=0.6)
    ax3.tick_params(labelsize=7.5)

    # ── Chart 4: Touch Type Performance Comparison ──
    ax4 = axes[1, 1]
    labels = ["0.0% Level", "POC Level"]
    t000_stats = res.touch_type_stats.get("0.0% Level", {})
    tpoc_stats = res.touch_type_stats.get("POC Level", {})

    wr_vals = [t000_stats.get("win_rate_pct", 0.0), tpoc_stats.get("win_rate_pct", 0.0)]
    pf_vals = [t000_stats.get("profit_factor", 0.0), tpoc_stats.get("profit_factor", 0.0)]

    idx = np.arange(len(labels))
    w = 0.32
    b1 = ax4.bar(idx - w / 2, wr_vals, width=w, label="Win Rate %", color="#0D9488", alpha=0.85)
    b2 = ax4.bar(idx + w / 2, [min(p * 15, 100) for p in pf_vals], width=w, label="Profit Factor (scaled)", color="#6366F1", alpha=0.85)

    ax4.set_xticks(idx)
    ax4.set_xticklabels([f"{l}\n(n={t000_stats.get('trades',0) if '0.0' in l else tpoc_stats.get('trades',0)})" for l in labels], fontsize=7.5)
    ax4.set_title("0.0% Touches vs POC Touches", fontsize=9.5, fontweight="bold", color="#1E293B", pad=6)
    ax4.legend(loc="upper right", fontsize=7.5, frameon=False)
    ax4.grid(True, linestyle=":", alpha=0.6, axis="y")
    ax4.tick_params(labelsize=7.5)

    for bar, val in zip(b1, wr_vals):
        ax4.annotate(f"{val:.1f}%", xy=(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5),
                     ha="center", fontsize=7, fontweight="bold", color="#0D9488")
    for bar, val in zip(b2, pf_vals):
        ax4.annotate(f"{val:.2f}x", xy=(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5),
                     ha="center", fontsize=7, fontweight="bold", color="#6366F1")

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def build_pdf_report(res: BacktestResult, output_path: Path | str) -> Path:
    """Build a comprehensive, publication-grade multi-page backtest PDF report."""
    out_p = Path(output_path).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_p),
        pagesize=A4,
        leftMargin=32,
        rightMargin=32,
        topMargin=36,
        bottomMargin=42,
    )

    styles = getSampleStyleSheet()

    # Custom typography styles
    style_title = ParagraphStyle(
        "FiboTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=C_NAVY_DARK,
    )
    style_subtitle = ParagraphStyle(
        "FiboSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13,
        textColor=C_SLATE,
    )
    style_h2 = ParagraphStyle(
        "FiboH2",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=15,
        textColor=C_NAVY_DARK,
        spaceBefore=10,
        spaceAfter=5,
    )
    style_meta_k = ParagraphStyle(
        "MetaK",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10.5,
        textColor=C_SLATE,
    )
    style_meta_v = ParagraphStyle(
        "MetaV",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=C_NAVY_DARK,
    )
    style_cell = ParagraphStyle(
        "CellNormal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=C_NAVY,
    )
    style_cell_bold = ParagraphStyle(
        "CellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=C_NAVY,
    )
    style_cell_win = ParagraphStyle(
        "CellWin",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=C_GREEN,
    )
    style_cell_loss = ParagraphStyle(
        "CellLoss",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=C_RED,
    )
    style_th = ParagraphStyle(
        "TableHead",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.white,
    )

    story = []

    # ── HEADER ──
    story.append(Paragraph("FIBO-STRATEGY — HISTORICAL BACKTEST SCANNER", style_title))
    story.append(Spacer(1, 2))
    story.append(Paragraph("Swing Fibonacci Arcs & Volume Profile [BigBeluga] Quantitative Performance Report", style_subtitle))
    story.append(Spacer(1, 8))

    # ── METADATA BANNER TABLE ──
    meta_data = [
        [
            Paragraph("<b>Backtest Period:</b>", style_meta_k),
            Paragraph(f"<b>{res.period_label}</b> ({res.start_date} to {res.end_date})", style_meta_v),
            Paragraph("<b>Universe:</b>", style_meta_k),
            Paragraph(f"{res.universe_name} ({res.total_symbols_scanned} stocks)", style_meta_v),
        ],
        [
            Paragraph("<b>Touch Filter:</b>", style_meta_k),
            Paragraph(f"<b>{res.touch_filter}</b>", style_meta_v),
            Paragraph("<b>Target / Stop / Hold:</b>", style_meta_k),
            Paragraph(f"+{res.target_pct:.1f}% TP / -{res.stop_pct:.1f}% SL (1:2 R:R) | Max {res.max_hold_days}d", style_meta_v),
        ],
        [
            Paragraph("<b>Generated On:</b>", style_meta_k),
            Paragraph(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST", style_meta_v),
            Paragraph("<b>Pine Equivalence:</b>", style_meta_k),
            Paragraph("Exact wick containment (low ≤ level ≤ high), zero tolerance", style_meta_v),
        ],
    ]
    meta_table = Table(meta_data, colWidths=[90, 180, 100, 160])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    # ── EXECUTIVE KPI CARDS TABLE ──
    def _card(title: str, val: str, sub: str, color_hex: str = "#2563EB"):
        return [
            Paragraph(f"<font color='{color_hex}'><b>{title}</b></font>", ParagraphStyle("CTitle", fontName="Helvetica-Bold", fontSize=8, leading=10)),
            Paragraph(f"<font color='#0F172A'><b>{val}</b></font>", ParagraphStyle("CVal", fontName="Helvetica-Bold", fontSize=15, leading=18)),
            Paragraph(f"<font color='#64748B'>{sub}</font>", ParagraphStyle("CSub", fontName="Helvetica", fontSize=7, leading=8.5)),
        ]

    c_wr_color = "#16A34A" if res.win_rate_pct >= 50 else ("#D97706" if res.win_rate_pct >= 35 else "#DC2626")
    c_pf_color = "#16A34A" if res.profit_factor >= 1.5 else ("#2563EB" if res.profit_factor >= 1.0 else "#DC2626")

    kpi_col1 = _card("TOTAL TOUCHES", f"{res.total_touches:,}", f"0.0%: {res.touches_000} | POC: {res.touches_poc}", "#2563EB")
    kpi_col2 = _card("WIN RATE", f"{res.win_rate_pct:.1f}%", f"{res.winning_trades}W - {res.losing_trades}L ({res.total_trades} trades)", c_wr_color)
    kpi_col3 = _card("PROFIT FACTOR", f"{res.profit_factor:.2f}x", f"Gross P/L Ratio", c_pf_color)
    kpi_col4 = _card("AVG RETURN", f"{res.avg_trade_return_pct:+.2f}%", f"Total: {res.total_strategy_return_pct:+.1f}%", "#0D9488")
    kpi_col5 = _card("MAX DRAWDOWN", f"-{res.max_drawdown_pct:.1f}%", f"Peak to Trough", "#DC2626")
    kpi_col6 = _card("AVG HOLDING", f"{res.avg_holding_days:.1f}d", f"Target Hit: {res.target_hit_rate_pct:.0f}%", "#7C3AED")

    kpi_data = [
        [kpi_col1[0], kpi_col2[0], kpi_col3[0], kpi_col4[0], kpi_col5[0], kpi_col6[0]],
        [kpi_col1[1], kpi_col2[1], kpi_col3[1], kpi_col4[1], kpi_col5[1], kpi_col6[1]],
        [kpi_col1[2], kpi_col2[2], kpi_col3[2], kpi_col4[2], kpi_col5[2], kpi_col6[2]],
    ]
    kpi_table = Table(kpi_data, colWidths=[88, 88, 88, 88, 88, 92])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), C_LIGHT_BG),
        ("BOX", (0, 0), (-1, -1), 0.8, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E2E8F0")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(kpi_table)
    story.append(Spacer(1, 10))

    # ── EMBEDDED CHARTS (4-PANEL) ──
    story.append(Paragraph("<b>PERFORMANCE CHARTS & ANALYTICS</b>", style_h2))
    chart_buf = _generate_charts(res)
    chart_img = Image(chart_buf, width=530, height=310)
    story.append(chart_img)
    story.append(Spacer(1, 12))

    # ── PAGE BREAK FOR TABLES & BREAKDOWNS ──
    story.append(PageBreak())

    # ── SECTION 1: FORWARD HORIZON ANALYSIS TABLE ──
    story.append(Paragraph("<b>FORWARD HORIZON PERFORMANCE (+1d, +3d, +5d, +10d, +20d)</b>", style_h2))
    story.append(Paragraph("Independent post-touch returns measuring raw price reaction across multiple holding horizons.", style_subtitle))
    story.append(Spacer(1, 4))

    horizon_rows = [
        [
            Paragraph("Horizon", style_th),
            Paragraph("Touches", style_th),
            Paragraph("Win Rate %", style_th),
            Paragraph("Avg Return %", style_th),
            Paragraph("Median %", style_th),
            Paragraph("Std Dev %", style_th),
            Paragraph("Best Trade %", style_th),
            Paragraph("Worst Trade %", style_th),
        ]
    ]
    for h_name in ["1-Day", "3-Day", "5-Day", "10-Day", "20-Day"]:
        h_data = res.horizon_stats.get(h_name, {})
        avg_r = h_data.get("avg_return_pct", 0.0)
        ret_style = style_cell_win if avg_r >= 0 else style_cell_loss
        horizon_rows.append([
            Paragraph(f"<b>+{h_name}</b>", style_cell_bold),
            Paragraph(str(h_data.get("count", 0)), style_cell),
            Paragraph(f"<b>{h_data.get('win_rate_pct', 0.0):.1f}%</b>", style_cell_bold),
            Paragraph(f"<b>{avg_r:+.2f}%</b>", ret_style),
            Paragraph(f"{h_data.get('median_return_pct', 0.0):+.2f}%", style_cell),
            Paragraph(f"{h_data.get('std_pct', 0.0):.2f}%", style_cell),
            Paragraph(f"{h_data.get('best_pct', 0.0):+.2f}%", style_cell_win),
            Paragraph(f"{h_data.get('worst_pct', 0.0):+.2f}%", style_cell_loss),
        ])

    h_table = Table(horizon_rows, colWidths=[65, 50, 70, 75, 65, 65, 70, 70])
    h_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        ("BOX", (0, 0), (-1, -1), 0.6, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(h_table)
    story.append(Spacer(1, 10))

    # ── SECTION 2: TOUCH TYPE BREAKDOWN TABLE ──
    story.append(Paragraph("<b>TOUCH TYPE COMPARISON: 0.0% LEVEL vs POC LEVEL</b>", style_h2))
    touch_comp_rows = [
        [
            Paragraph("Level Type", style_th),
            Paragraph("Trades", style_th),
            Paragraph("Win Rate %", style_th),
            Paragraph("Avg Return %", style_th),
            Paragraph("Profit Factor", style_th),
            Paragraph("Avg MFE %", style_th),
            Paragraph("Avg MAE %", style_th),
        ]
    ]
    for label, key in [("0.0% Level (Swing Anchor)", "0.0% Level"),
                       ("POC Level (Volume Profile)", "POC Level")]:
        st = res.touch_type_stats.get(key, {})
        ar = st.get("avg_return_pct", 0.0)
        r_style = style_cell_win if ar >= 0 else style_cell_loss
        touch_comp_rows.append([
            Paragraph(f"<b>{label}</b>", style_cell_bold),
            Paragraph(str(st.get("trades", 0)), style_cell),
            Paragraph(f"<b>{st.get('win_rate_pct', 0.0):.1f}%</b>", style_cell_bold),
            Paragraph(f"<b>{ar:+.2f}%</b>", r_style),
            Paragraph(f"<b>{st.get('profit_factor', 0.0):.2f}x</b>", style_cell_bold),
            Paragraph(f"+{st.get('avg_mfe_pct', 0.0):.2f}%", style_cell_win),
            Paragraph(f"{st.get('avg_mae_pct', 0.0):.2f}%", style_cell_loss),
        ])

    tc_table = Table(touch_comp_rows, colWidths=[150, 60, 65, 65, 65, 60, 65])
    tc_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        ("BOX", (0, 0), (-1, -1), 0.6, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(tc_table)
    story.append(Spacer(1, 10))

    # ── SECTION 3: TOP STOCKS PERFORMANCE TABLE ──
    story.append(Paragraph("<b>STOCK-BY-STOCK PERFORMANCE BREAKDOWN (TOP 15 STOCKS)</b>", style_h2))
    stock_rows = [
        [
            Paragraph("Symbol", style_th),
            Paragraph("Touches", style_th),
            Paragraph("Trades", style_th),
            Paragraph("Win Rate %", style_th),
            Paragraph("Avg Return %", style_th),
            Paragraph("Total P&L %", style_th),
            Paragraph("Profit Factor", style_th),
            Paragraph("Best Trade %", style_th),
            Paragraph("Worst Trade %", style_th),
        ]
    ]

    top_stocks = res.stock_performance[:15]
    for sp in top_stocks:
        ret_s = style_cell_win if sp.total_return_pct >= 0 else style_cell_loss
        stock_rows.append([
            Paragraph(f"<b>{sp.symbol}</b>", style_cell_bold),
            Paragraph(str(sp.total_touches), style_cell),
            Paragraph(str(sp.total_trades), style_cell),
            Paragraph(f"<b>{sp.win_rate_pct:.1f}%</b>", style_cell_bold),
            Paragraph(f"{sp.avg_return_pct:+.2f}%", ret_s),
            Paragraph(f"<b>{sp.total_return_pct:+.1f}%</b>", ret_s),
            Paragraph(f"{sp.profit_factor:.2f}x", style_cell),
            Paragraph(f"{sp.best_trade_pct:+.1f}%", style_cell_win),
            Paragraph(f"{sp.worst_trade_pct:+.1f}%", style_cell_loss),
        ])

    st_table = Table(stock_rows, colWidths=[80, 50, 50, 65, 65, 65, 55, 50, 50])
    st_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        ("BOX", (0, 0), (-1, -1), 0.6, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(st_table)
    story.append(Spacer(1, 10))

    # ── PAGE BREAK FOR DETAILED TRADE LOG ──
    story.append(PageBreak())

    # ── SECTION 4: HISTORICAL TOUCH EVENT LOG ──
    story.append(Paragraph("<b>HISTORICAL TOUCH TRADE LOG (SAMPLE OF KEY TRADES)</b>", style_h2))
    story.append(Paragraph(f"Displaying sample of {min(35, len(res.trade_log))} trades out of {len(res.trade_log)} total events.", style_subtitle))
    story.append(Spacer(1, 4))

    log_rows = [
        [
            Paragraph("Entry Date", style_th),
            Paragraph("Symbol", style_th),
            Paragraph("Touch Type", style_th),
            Paragraph("Swing", style_th),
            Paragraph("Level Px", style_th),
            Paragraph("Entry CMP", style_th),
            Paragraph("Exit Date", style_th),
            Paragraph("Outcome", style_th),
            Paragraph("Return %", style_th),
            Paragraph("Hold", style_th),
        ]
    ]

    sample_trades = res.trade_log[:35]
    for t in sample_trades:
        ret_s = style_cell_win if t.return_pct > 0 else (style_cell_loss if t.return_pct < 0 else style_cell)
        outcome_color = C_GREEN if t.outcome == "TARGET" else (C_RED if t.outcome == "STOP_LOSS" else C_SLATE)
        log_rows.append([
            Paragraph(t.entry_date, style_cell),
            Paragraph(f"<b>{t.symbol}</b>", style_cell_bold),
            Paragraph(t.touch_type.replace(" Level", ""), style_cell),
            Paragraph(f"<b>{t.swing_direction}</b>", style_cell),
            Paragraph(f"{t.level_price:.1f}", style_cell),
            Paragraph(f"{t.entry_price:.1f}", style_cell),
            Paragraph(t.exit_date, style_cell),
            Paragraph(f"<font color='{outcome_color.hexval()}'><b>{t.outcome}</b></font>", style_cell_bold),
            Paragraph(f"<b>{t.return_pct:+.1f}%</b>", ret_s),
            Paragraph(f"{t.holding_days}d", style_cell),
        ])

    log_table = Table(log_rows, colWidths=[55, 60, 60, 45, 55, 55, 55, 60, 50, 35])
    log_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), C_NAVY),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_LIGHT_BG]),
        ("BOX", (0, 0), (-1, -1), 0.6, C_BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(log_table)
    story.append(Spacer(1, 10))

    # ── METHODOLOGY & DISCLAIMER ──
    story.append(Paragraph("<b>Methodology & Indicator Equivalence Notes:</b>", style_meta_k))
    story.append(Paragraph(
        "1. <b>Zero Tolerance Wick Containment:</b> A touch requires exact containment <code>low ≤ level ≤ high</code> with zero tolerance. "
        "2. <b>Prior-Bar Anchor Rule:</b> Retest of swing anchor (0.0% level) automatically flips swing direction; scanner tests both current and yesterday's drawn anchor. "
        "3. <b>Volume Profile:</b> Exact replica of TradingView 100-bin triangular distribution from swing anchor to current bar. "
        "4. <b>Bull-Side Only (Live-Scanner Rules):</b> Only bullish-leg signals (anchor = swing LOW) are evaluated and simulated as LONG trades — identical "
        "to the live scanner. Bear-leg SHORT simulation is excluded; no trade cooldown (live re-arms each trading day); engine runs on 5y of daily context. "
        "5. <b>Disclaimer:</b> Educational and quantitative research report. Past performance does not guarantee future results. Verify on TradingView chart before executing.",
        ParagraphStyle("DiscStyle", fontName="Helvetica", fontSize=6.5, leading=8.5, textColor=C_SLATE)
    ))

    doc.build(story, canvasmaker=NumberedCanvas)
    log.info("PDF report successfully written to %s (%d bytes)", out_p, out_p.stat().st_size)
    return out_p

#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════════════════════
# web_app.py — Interactive Web UI Dashboard for FIBO Historical Backtest
# ══════════════════════════════════════════════════════════════════════════════
# Serves an interactive trading terminal on 0.0.0.0:5000:
#   • 2 Touch options: 1) 0.0% touches  2) POC touches  (plus Both)
#   • Period selection: 1yr, 2yr, 3yr, 5yr
#   • Stock Universe selection: Full NSE, Nifty 50, Nifty 100, Custom
#   • Interactive KPI cards, charts, stock performance ranking, trade log
#   • 1-Click PDF Report download & in-browser preview
# ══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template_string, request, send_file

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest_engine import BacktestResult, run_historical_backtest
from data_manager import get_universe_symbols, load_batch_history
from pdf_report import build_pdf_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fibo.web")

app = Flask(__name__)
# Permit all hosts/origins for preview proxy
app.config["TEMPLATES_AUTO_RELOAD"] = True

REPORTS_DIR = ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory storage for latest backtest state
CURRENT_STATE: Dict[str, Any] = {
    "status": "idle",       # 'idle', 'running', 'completed', 'error'
    "progress": 0,
    "message": "Ready to run backtest.",
    "result": None,
    "pdf_filename": None,
    "last_updated": None,
}

# Pre-cache initial run on startup or on demand
INITIAL_LOCK = threading.Lock()


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>FIBO-STRATEGY — Historical Backtest Scanner</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    .table-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .table-scroll::-webkit-scrollbar-track { background: #1e293b; }
    .table-scroll::-webkit-scrollbar-thumb { background: #475569; border-radius: 3px; }
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen">

  <!-- TOP NAVIGATION -->
  <header class="border-b border-slate-800 bg-slate-900/90 backdrop-blur sticky top-0 z-50 px-6 py-3.5 flex items-center justify-between">
    <div class="flex items-center gap-3">
      <div class="w-9 h-9 rounded-lg bg-gradient-to-tr from-blue-600 to-teal-400 flex items-center justify-center font-black text-white text-lg shadow-lg shadow-blue-500/20">
        Φ
      </div>
      <div>
        <h1 class="text-base font-bold text-white flex items-center gap-2">
          FIBO-STRATEGY <span class="text-xs px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 font-mono">BACKTEST SCANNER</span>
        </h1>
        <p class="text-xs text-slate-400">Swing Fibonacci Arcs & Volume Profile — Historical Stock Performance</p>
      </div>
    </div>
    
    <div class="flex items-center gap-3">
      <span id="marketStatusBadge" class="text-xs px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> Pine v6 Exact Replicant
      </span>
      <a href="#reportsSection" class="text-xs px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 flex items-center gap-1.5 transition">
        <i class="fa-regular fa-file-pdf text-red-400"></i> Reports Vault
      </a>
    </div>
  </header>

  <!-- MAIN CONTAINER -->
  <div class="max-w-7xl mx-auto px-4 sm:px-6 py-6 grid grid-cols-1 lg:grid-cols-12 gap-6">

    <!-- LEFT COLUMN: CONTROLS & CONFIGURATION (4 COLS) -->
    <div class="lg:col-span-4 space-y-6">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-5">
        
        <div class="flex items-center justify-between pb-3 border-b border-slate-800">
          <h2 class="font-bold text-sm text-slate-200 flex items-center gap-2">
            <i class="fa-solid fa-sliders text-blue-400"></i> Scanner Options
          </h2>
          <span class="text-[11px] text-slate-400">Historical Parameters</span>
        </div>

        <!-- 1) TOUCH OPTION SELECTION -->
        <div>
          <label class="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
            1. Touch Condition (2 Options)
          </label>
          <div class="grid grid-cols-1 gap-2">
            <label class="flex items-center gap-3 p-2.5 rounded-lg border border-slate-800 bg-slate-800/40 hover:bg-slate-800/80 cursor-pointer transition">
              <input type="radio" name="touchFilter" value="0.0%" class="text-blue-500 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white flex items-center gap-1.5">
                  <span class="w-2 h-2 rounded-full bg-cyan-400"></span> 0.0% Touches
                </div>
                <div class="text-[11px] text-slate-400">Price touches swing anchor level (support / resistance test)</div>
              </div>
            </label>

            <label class="flex items-center gap-3 p-2.5 rounded-lg border border-slate-800 bg-slate-800/40 hover:bg-slate-800/80 cursor-pointer transition">
              <input type="radio" name="touchFilter" value="poc" class="text-blue-500 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-white flex items-center gap-1.5">
                  <span class="w-2 h-2 rounded-full bg-yellow-400"></span> POC Touches
                </div>
                <div class="text-[11px] text-slate-400">Price wicks Volume Profile Point of Control</div>
              </div>
            </label>

            <label class="flex items-center gap-3 p-2.5 rounded-lg border border-blue-500/40 bg-blue-500/10 cursor-pointer transition">
              <input type="radio" name="touchFilter" value="both" checked class="text-blue-500 focus:ring-0">
              <div>
                <div class="text-xs font-semibold text-blue-300 flex items-center gap-1.5">
                  <span class="w-2 h-2 rounded-full bg-blue-400"></span> Both (0.0% & POC Touches)
                </div>
                <div class="text-[11px] text-slate-400">Evaluates combined performance of all touches</div>
              </div>
            </label>
          </div>
          <div class="mt-2 flex items-center gap-1.5 text-[11px] text-emerald-400/90">
            <i class="fa-solid fa-arrow-trend-up"></i>
            <span>Bull-side LONG only — signals &amp; rules identical to the live scanner (no cooldown, 5y context)</span>
          </div>
        </div>

        <!-- 2) PERIOD SELECTION -->
        <div>
          <label class="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
            2. Backtest Duration
          </label>
          <div class="grid grid-cols-4 gap-2">
            <button type="button" onclick="selectPeriod('1y')" id="pBtn_1y" class="period-btn py-2 text-xs font-bold rounded-lg border border-slate-700 bg-slate-800 text-slate-300 hover:border-blue-500 transition">
              1yr
            </button>
            <button type="button" onclick="selectPeriod('2y')" id="pBtn_2y" class="period-btn py-2 text-xs font-bold rounded-lg border border-blue-500 bg-blue-600/20 text-blue-300 transition">
              2yr
            </button>
            <button type="button" onclick="selectPeriod('3y')" id="pBtn_3y" class="period-btn py-2 text-xs font-bold rounded-lg border border-slate-700 bg-slate-800 text-slate-300 hover:border-blue-500 transition">
              3yr
            </button>
            <button type="button" onclick="selectPeriod('5y')" id="pBtn_5y" class="period-btn py-2 text-xs font-bold rounded-lg border border-slate-700 bg-slate-800 text-slate-300 hover:border-blue-500 transition">
              5yr
            </button>
          </div>
          <input type="hidden" id="selectedPeriod" value="2y">
        </div>

        <!-- 3) UNIVERSE SELECTION -->
        <div>
          <label class="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">
            3. Stock Universe
          </label>
          <select id="universeSelect" onchange="toggleCustomSymbols()" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-blue-500">
            <option value="nifty50" selected>Nifty 50 (Top Liquid Indian Large Caps)</option>
            <option value="nifty100">Nifty 100 Index</option>
            <option value="full">Full Filtered NSE Universe (>₹1,000 Cr Mcap, Price ≥ ₹100)</option>
            <option value="sample">Quick Sample (Top 20 Stocks)</option>
            <option value="custom">Custom Stocks (Enter Symbols)</option>
          </select>
          
          <div id="customSymbolsBox" class="mt-2 hidden">
            <input type="text" id="customSymbolsInput" placeholder="e.g. RELIANCE, TCS, INFY, HDFCBANK" class="w-full bg-slate-950 border border-slate-700 rounded-lg px-3 py-2 text-xs text-white placeholder-slate-500">
          </div>
        </div>

        <!-- 4) STRATEGY SETTINGS (COLLAPSIBLE) -->
        <div class="border border-slate-800 rounded-lg p-3 bg-slate-950/60">
          <button type="button" onclick="document.getElementById('stratSettings').classList.toggle('hidden')" class="w-full flex items-center justify-between text-xs font-semibold text-slate-300">
            <span><i class="fa-solid fa-gear text-slate-400 mr-1.5"></i> Trade Parameters (1:2 R:R)</span>
            <i class="fa-solid fa-chevron-down text-[10px]"></i>
          </button>
          <div id="stratSettings" class="mt-3 space-y-3 pt-3 border-t border-slate-800/80">
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-[11px] text-slate-400 mb-1">Target Profit %</label>
                <input type="number" step="0.5" id="targetPct" value="4.0" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs text-white">
              </div>
              <div>
                <label class="block text-[11px] text-slate-400 mb-1">Stop Loss %</label>
                <input type="number" step="0.5" id="stopPct" value="2.0" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs text-white">
              </div>
            </div>
            <div>
              <label class="block text-[11px] text-slate-400 mb-1">Max Holding Period (Days)</label>
              <input type="number" id="maxHold" value="10" class="w-full bg-slate-900 border border-slate-700 rounded px-2 py-1.5 text-xs text-white">
            </div>
          </div>
        </div>

        <!-- RUN BUTTON & STATUS -->
        <div class="pt-2">
          <button type="button" id="runBtn" onclick="runBacktest()" class="w-full py-3 px-4 rounded-xl bg-gradient-to-r from-blue-600 hover:from-blue-500 to-indigo-600 text-white font-bold text-xs uppercase tracking-wider shadow-lg shadow-blue-500/25 transition flex items-center justify-center gap-2">
            <i class="fa-solid fa-play"></i> Run Historical Backtest
          </button>
          
          <div id="progressBarBox" class="mt-3 hidden">
            <div class="flex justify-between text-[11px] text-slate-400 mb-1">
              <span id="statusMsg">Processing historical bars...</span>
              <span id="progressPct">0%</span>
            </div>
            <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
              <div id="progressBar" class="bg-blue-500 h-1.5 rounded-full transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>
        </div>

      </div>

      <!-- ACTIVE PDF EXPORT BOX -->
      <div id="pdfExportBox" class="bg-gradient-to-br from-slate-900 to-blue-950/40 border border-blue-500/30 rounded-xl p-5 shadow-xl">
        <div class="flex items-start justify-between">
          <div>
            <div class="text-xs font-bold text-blue-400 uppercase tracking-wider mb-1 flex items-center gap-1.5">
              <i class="fa-solid fa-file-circle-check text-emerald-400"></i> PDF Report Ready
            </div>
            <div id="pdfReportName" class="text-xs text-slate-200 font-mono font-medium truncate max-w-[200px]">
              fibo_backtest_report.pdf
            </div>
          </div>
          <span class="text-[10px] bg-blue-500/20 text-blue-300 px-2 py-0.5 rounded border border-blue-500/30 font-semibold">Institutional Grade</span>
        </div>
        <div class="grid grid-cols-2 gap-2 mt-4">
          <a id="pdfDownloadLink" href="/reports/latest.pdf" target="_blank" download class="py-2 px-3 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold text-xs text-center flex items-center justify-center gap-1.5 transition">
            <i class="fa-solid fa-download"></i> Download PDF
          </a>
          <a id="pdfViewLink" href="/reports/latest.pdf" target="_blank" class="py-2 px-3 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-xs text-center flex items-center justify-center gap-1.5 transition border border-slate-700">
            <i class="fa-solid fa-eye"></i> View Online
          </a>
        </div>
      </div>

    </div>

    <!-- RIGHT COLUMN: RESULTS DASHBOARD (8 COLS) -->
    <div class="lg:col-span-8 space-y-6">

      <!-- TOP KPI CARDS -->
      <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-3">
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Touches</div>
          <div id="kpiTouches" class="text-xl font-black text-white mt-1">--</div>
          <div id="kpiTouchesSub" class="text-[10px] text-slate-500 truncate mt-0.5">0.0%: 0 | POC: 0</div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Win Rate</div>
          <div id="kpiWinRate" class="text-xl font-black text-emerald-400 mt-1">--%</div>
          <div id="kpiTradesSub" class="text-[10px] text-slate-500 truncate mt-0.5">0W - 0L (0 total)</div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Profit Factor</div>
          <div id="kpiProfitFactor" class="text-xl font-black text-blue-400 mt-1">--x</div>
          <div class="text-[10px] text-slate-500 truncate mt-0.5">Gross Gain / Loss</div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Avg Return</div>
          <div id="kpiAvgReturn" class="text-xl font-black text-teal-400 mt-1">--%</div>
          <div id="kpiTotalReturn" class="text-[10px] text-slate-500 truncate mt-0.5">Total: --%</div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Max DD</div>
          <div id="kpiMaxDD" class="text-xl font-black text-rose-400 mt-1">--%</div>
          <div class="text-[10px] text-slate-500 truncate mt-0.5">Peak-to-Trough</div>
        </div>

        <div class="bg-slate-900 border border-slate-800 rounded-xl p-3.5">
          <div class="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">Avg Hold</div>
          <div id="kpiAvgHold" class="text-xl font-black text-purple-400 mt-1">--d</div>
          <div id="kpiTargetRate" class="text-[10px] text-slate-500 truncate mt-0.5">Target Hit: --%</div>
        </div>
      </div>

      <!-- CHARTS SECTION: EQUITY CURVE + FORWARD HORIZONS -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
        
        <!-- EQUITY CURVE CHART -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-xl">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
              <i class="fa-solid fa-chart-line text-blue-400"></i> Strategy Growth (Base 100)
            </h3>
            <span class="text-[10px] text-slate-400" id="equityDates">--</span>
          </div>
          <div class="h-52 relative">
            <canvas id="equityChart"></canvas>
          </div>
        </div>

        <!-- FORWARD HORIZON BAR CHART -->
        <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-xl">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
              <i class="fa-solid fa-chart-simple text-emerald-400"></i> Forward Horizons (+1d → +20d)
            </h3>
            <span class="text-[10px] text-slate-400">Post-Touch Returns</span>
          </div>
          <div class="h-52 relative">
            <canvas id="horizonChart"></canvas>
          </div>
        </div>

      </div>

      <!-- STOCK-BY-STOCK PERFORMANCE RANKING -->
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-xl">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
            <i class="fa-solid fa-trophy text-amber-400"></i> Stock Performance Ranking
          </h3>
          <div class="flex items-center gap-2">
            <input type="text" id="stockSearchInput" onkeyup="filterStockTable()" placeholder="Search stock..." class="bg-slate-950 border border-slate-700 rounded px-2.5 py-1 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500">
          </div>
        </div>

        <div class="overflow-x-auto table-scroll max-h-64">
          <table class="w-full text-left text-xs text-slate-300">
            <thead class="bg-slate-950/80 sticky top-0 text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-800">
              <tr>
                <th class="py-2.5 px-3">Symbol</th>
                <th class="py-2.5 px-3 text-center">Touches</th>
                <th class="py-2.5 px-3 text-center">Trades</th>
                <th class="py-2.5 px-3 text-center">Win Rate</th>
                <th class="py-2.5 px-3 text-right">Avg Ret</th>
                <th class="py-2.5 px-3 text-right">Total P&L</th>
                <th class="py-2.5 px-3 text-center">Profit Factor</th>
                <th class="py-2.5 px-3 text-right">Best</th>
                <th class="py-2.5 px-3 text-right">Worst</th>
              </tr>
            </thead>
            <tbody id="stockTableBody" class="divide-y divide-slate-800/60 font-mono">
              <tr>
                <td colspan="9" class="py-6 text-center text-slate-500 font-sans">No data loaded yet. Run a backtest to see stock performance.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- RECENT HISTORICAL TRADES LOG -->
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-4 shadow-xl">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider flex items-center gap-2">
            <i class="fa-solid fa-list-check text-cyan-400"></i> Historical Touch Event Log
          </h3>
          <span class="text-[11px] text-slate-400" id="tradeLogCount">Showing key trades</span>
        </div>

        <div class="overflow-x-auto table-scroll max-h-64">
          <table class="w-full text-left text-xs text-slate-300">
            <thead class="bg-slate-950/80 sticky top-0 text-[11px] uppercase tracking-wider text-slate-400 border-b border-slate-800">
              <tr>
                <th class="py-2.5 px-3">Entry Date</th>
                <th class="py-2.5 px-3">Symbol</th>
                <th class="py-2.5 px-3">Touch Type</th>
                <th class="py-2.5 px-3 text-center">Swing</th>
                <th class="py-2.5 px-3 text-right">Level Px</th>
                <th class="py-2.5 px-3 text-right">CMP</th>
                <th class="py-2.5 px-3">Exit Date</th>
                <th class="py-2.5 px-3 text-center">Outcome</th>
                <th class="py-2.5 px-3 text-right">Return %</th>
                <th class="py-2.5 px-3 text-center">Hold</th>
              </tr>
            </thead>
            <tbody id="tradeLogBody" class="divide-y divide-slate-800/60 font-mono text-[11px]">
              <tr>
                <td colspan="10" class="py-6 text-center text-slate-500 font-sans">No trades logged yet.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </div>

  </div>

  <!-- JAVASCRIPT LOGIC -->
  <script>
    let equityChartInstance = null;
    let horizonChartInstance = null;
    let allStockRecords = [];

    function selectPeriod(period) {
      document.getElementById('selectedPeriod').value = period;
      document.querySelectorAll('.period-btn').forEach(btn => {
        btn.classList.remove('border-blue-500', 'bg-blue-600/20', 'text-blue-300');
        btn.classList.add('border-slate-700', 'bg-slate-800', 'text-slate-300');
      });
      const activeBtn = document.getElementById('pBtn_' + period);
      if (activeBtn) {
        activeBtn.classList.remove('border-slate-700', 'bg-slate-800', 'text-slate-300');
        activeBtn.classList.add('border-blue-500', 'bg-blue-600/20', 'text-blue-300');
      }
    }

    function toggleCustomSymbols() {
      const u = document.getElementById('universeSelect').value;
      const box = document.getElementById('customSymbolsBox');
      if (u === 'custom') {
        box.classList.remove('hidden');
      } else {
        box.classList.add('hidden');
      }
    }

    async function runBacktest() {
      const touchFilter = document.querySelector('input[name="touchFilter"]:checked').value;
      const period = document.getElementById('selectedPeriod').value;
      const universe = document.getElementById('universeSelect').value;
      const customSymbols = document.getElementById('customSymbolsInput').value;
      const targetPct = parseFloat(document.getElementById('targetPct').value) || 4.0;
      const stopPct = parseFloat(document.getElementById('stopPct').value) || 2.0;
      const maxHold = parseInt(document.getElementById('maxHold').value) || 10;

      const runBtn = document.getElementById('runBtn');
      const progressBarBox = document.getElementById('progressBarBox');
      const progressBar = document.getElementById('progressBar');
      const statusMsg = document.getElementById('statusMsg');
      const progressPct = document.getElementById('progressPct');

      runBtn.disabled = true;
      runBtn.classList.add('opacity-50', 'cursor-not-allowed');
      runBtn.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-2"></i> Scanning Historical Swings...';
      progressBarBox.classList.remove('hidden');
      progressBar.style.width = '20%';
      progressPct.innerText = '20%';
      statusMsg.innerText = 'Resolving stock universe & causal levels...';

      try {
        const payload = {
          touch_filter: touchFilter,
          period: period,
          universe: universe,
          custom_symbols: customSymbols,
          target_pct: targetPct,
          stop_pct: stopPct,
          max_hold: maxHold,
        };

        const resp = await fetch('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });

        progressBar.style.width = '80%';
        progressPct.innerText = '80%';
        statusMsg.innerText = 'Compiling PDF report & metrics...';

        const data = await resp.json();
        if (data.status === 'success') {
          progressBar.style.width = '100%';
          progressPct.innerText = '100%';
          statusMsg.innerText = 'Completed successfully!';
          renderResults(data.result, data.pdf_filename);
        } else {
          alert('Backtest failed: ' + (data.message || 'Unknown error'));
        }
      } catch (err) {
        console.error(err);
        alert('Network or execution error while running backtest.');
      } finally {
        runBtn.disabled = false;
        runBtn.classList.remove('opacity-50', 'cursor-not-allowed');
        runBtn.innerHTML = '<i class="fa-solid fa-play mr-2"></i> Run Historical Backtest';
        setTimeout(() => progressBarBox.classList.add('hidden'), 2000);
      }
    }

    function renderResults(res, pdfFilename) {
      if (!res) return;

      // Update KPI Cards
      document.getElementById('kpiTouches').innerText = Number(res.total_touches).toLocaleString();
      document.getElementById('kpiTouchesSub').innerText = `0.0%: ${res.touches_000} | POC: ${res.touches_poc}`;

      const wrEl = document.getElementById('kpiWinRate');
      wrEl.innerText = res.win_rate_pct.toFixed(1) + '%';
      wrEl.className = 'text-xl font-black mt-1 ' + (res.win_rate_pct >= 50 ? 'text-emerald-400' : (res.win_rate_pct >= 35 ? 'text-amber-400' : 'text-rose-400'));
      document.getElementById('kpiTradesSub').innerText = `${res.winning_trades}W - ${res.losing_trades}L (${res.total_trades} total)`;

      const pfEl = document.getElementById('kpiProfitFactor');
      pfEl.innerText = res.profit_factor.toFixed(2) + 'x';
      pfEl.className = 'text-xl font-black mt-1 ' + (res.profit_factor >= 1.5 ? 'text-emerald-400' : (res.profit_factor >= 1.0 ? 'text-blue-400' : 'text-rose-400'));

      const avgRetEl = document.getElementById('kpiAvgReturn');
      const retVal = res.avg_trade_return_pct;
      avgRetEl.innerText = (retVal >= 0 ? '+' : '') + retVal.toFixed(2) + '%';
      avgRetEl.className = 'text-xl font-black mt-1 ' + (retVal >= 0 ? 'text-teal-400' : 'text-rose-400');
      document.getElementById('kpiTotalReturn').innerText = `Total: ${(res.total_strategy_return_pct >= 0 ? '+' : '')}${res.total_strategy_return_pct.toFixed(1)}%`;

      document.getElementById('kpiMaxDD').innerText = '-' + res.max_drawdown_pct.toFixed(1) + '%';
      document.getElementById('kpiAvgHold').innerText = res.avg_holding_days.toFixed(1) + 'd';
      document.getElementById('kpiTargetRate').innerText = `Target Hit: ${res.target_hit_rate_pct.toFixed(0)}%`;

      // Update PDF Export Box
      if (pdfFilename) {
        document.getElementById('pdfReportName').innerText = pdfFilename;
        document.getElementById('pdfDownloadLink').href = '/reports/' + pdfFilename;
        document.getElementById('pdfViewLink').href = '/reports/' + pdfFilename;
      }

      // Update Equity Curve Chart
      renderEquityChart(res.equity_curve);

      // Update Forward Horizon Bar Chart
      renderHorizonChart(res.horizon_stats);

      // Update Stock Performance Table
      renderStockTable(res.stock_performance || []);

      // Update Trade Log Table
      renderTradeLog(res.trade_log || []);
    }

    function renderEquityChart(curve) {
      const ctx = document.getElementById('equityChart').getContext('2d');
      if (equityChartInstance) equityChartInstance.destroy();

      const labels = (curve || []).map(c => c.date);
      const data = (curve || []).map(c => c.strategy_equity);

      equityChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Strategy Equity',
            data: data,
            borderColor: '#3b82f6',
            backgroundColor: 'rgba(59, 130, 246, 0.1)',
            fill: true,
            borderWidth: 2,
            tension: 0.2,
            pointRadius: 0,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              grid: { color: '#1e293b' },
              ticks: { color: '#64748b', maxTicksLimit: 6, font: { size: 10 } }
            },
            y: {
              grid: { color: '#1e293b' },
              ticks: { color: '#64748b', font: { size: 10 } }
            }
          }
        }
      });
    }

    function renderHorizonChart(stats) {
      const ctx = document.getElementById('horizonChart').getContext('2d');
      if (horizonChartInstance) horizonChartInstance.destroy();

      const horizons = ['1-Day', '3-Day', '5-Day', '10-Day', '20-Day'];
      const avgRets = horizons.map(h => (stats && stats[h]) ? stats[h].avg_return_pct : 0.0);
      const winRates = horizons.map(h => (stats && stats[h]) ? stats[h].win_rate_pct : 0.0);

      const colors = avgRets.map(r => r >= 0 ? '#10b981' : '#ef4444');

      horizonChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: horizons.map((h, i) => `+${h} (${winRates[i].toFixed(0)}% W)`),
          datasets: [{
            data: avgRets,
            backgroundColor: colors,
            borderRadius: 4,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false } },
          scales: {
            x: {
              grid: { display: false },
              ticks: { color: '#94a3b8', font: { size: 10 } }
            },
            y: {
              grid: { color: '#1e293b' },
              ticks: {
                color: '#64748b',
                font: { size: 10 },
                callback: v => (v >= 0 ? '+' : '') + v + '%'
              }
            }
          }
        }
      });
    }

    function renderStockTable(stocks) {
      allStockRecords = stocks;
      const tbody = document.getElementById('stockTableBody');
      if (!stocks || stocks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="py-6 text-center text-slate-500 font-sans">No stock records found.</td></tr>';
        return;
      }

      let html = '';
      stocks.forEach(s => {
        const pnlColor = s.total_return_pct >= 0 ? 'text-emerald-400' : 'text-rose-400';
        const avgColor = s.avg_return_pct >= 0 ? 'text-emerald-400' : 'text-rose-400';
        html += `
          <tr class="hover:bg-slate-800/40 transition">
            <td class="py-2 px-3 font-bold text-white">${s.symbol}</td>
            <td class="py-2 px-3 text-center text-slate-300">${s.total_touches}</td>
            <td class="py-2 px-3 text-center text-slate-300">${s.total_trades}</td>
            <td class="py-2 px-3 text-center font-semibold ${s.win_rate_pct >= 50 ? 'text-emerald-400' : 'text-slate-300'}">${s.win_rate_pct.toFixed(1)}%</td>
            <td class="py-2 px-3 text-right ${avgColor}">${(s.avg_return_pct >= 0 ? '+' : '')}${s.avg_return_pct.toFixed(2)}%</td>
            <td class="py-2 px-3 text-right font-bold ${pnlColor}">${(s.total_return_pct >= 0 ? '+' : '')}${s.total_return_pct.toFixed(1)}%</td>
            <td class="py-2 px-3 text-center text-slate-300">${s.profit_factor.toFixed(2)}x</td>
            <td class="py-2 px-3 text-right text-emerald-400">${(s.best_trade_pct >= 0 ? '+' : '')}${s.best_trade_pct.toFixed(1)}%</td>
            <td class="py-2 px-3 text-right text-rose-400">${s.worst_trade_pct.toFixed(1)}%</td>
          </tr>
        `;
      });
      tbody.innerHTML = html;
    }

    function filterStockTable() {
      const q = document.getElementById('stockSearchInput').value.trim().toUpperCase();
      if (!q) {
        renderStockTable(allStockRecords);
        return;
      }
      const filtered = allStockRecords.filter(s => s.symbol.toUpperCase().includes(q));
      renderStockTable(filtered);
    }

    function renderTradeLog(trades) {
      const tbody = document.getElementById('tradeLogBody');
      const countEl = document.getElementById('tradeLogCount');
      if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="py-6 text-center text-slate-500 font-sans">No trades recorded.</td></tr>';
        countEl.innerText = '0 trades';
        return;
      }

      countEl.innerText = `Showing ${Math.min(50, trades.length)} of ${trades.length} trades`;
      let html = '';
      trades.slice(0, 50).forEach(t => {
        const retColor = t.return_pct > 0 ? 'text-emerald-400' : (t.return_pct < 0 ? 'text-rose-400' : 'text-slate-400');
        const badgeColor = t.outcome === 'TARGET' ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30' : (t.outcome === 'STOP_LOSS' ? 'bg-rose-500/20 text-rose-400 border border-rose-500/30' : 'bg-slate-700/50 text-slate-300 border border-slate-600');
        html += `
          <tr class="hover:bg-slate-800/40 transition">
            <td class="py-1.5 px-3 text-slate-400">${t.entry_date}</td>
            <td class="py-1.5 px-3 font-bold text-white">${t.symbol}</td>
            <td class="py-1.5 px-3 text-slate-300">${t.touch_type.replace(' Level', '')}</td>
            <td class="py-1.5 px-3 text-center text-slate-300">${t.swing_direction}</td>
            <td class="py-1.5 px-3 text-right text-slate-400">${t.level_price.toFixed(1)}</td>
            <td class="py-1.5 px-3 text-right text-slate-300">${t.entry_price.toFixed(1)}</td>
            <td class="py-1.5 px-3 text-slate-400">${t.exit_date}</td>
            <td class="py-1.5 px-3 text-center"><span class="px-1.5 py-0.5 rounded text-[10px] font-bold ${badgeColor}">${t.outcome}</span></td>
            <td class="py-1.5 px-3 text-right font-bold ${retColor}">${(t.return_pct >= 0 ? '+' : '')}${t.return_pct.toFixed(1)}%</td>
            <td class="py-1.5 px-3 text-center text-slate-400">${t.holding_days}d</td>
          </tr>
        `;
      });
      tbody.innerHTML = html;
    }

    // Load initial sample backtest data on page load
    window.addEventListener('DOMContentLoaded', async () => {
      try {
        const resp = await fetch('/api/initial-data');
        const data = await resp.json();
        if (data && data.result) {
          renderResults(data.result, data.pdf_filename);
        }
      } catch (e) {
        console.warn('Initial data load:', e);
      }
    });
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/run", methods=["POST"])
def api_run_backtest():
    data = request.get_json() or {}
    touch_filter = data.get("touch_filter", "both")
    period = data.get("period", "2y")
    universe = data.get("universe", "nifty50")
    custom_symbols = data.get("custom_symbols", "")
    target_pct = float(data.get("target_pct", 4.0))
    stop_pct = float(data.get("stop_pct", 2.0))
    max_hold = int(data.get("max_hold", 10))

    if universe == "custom" and custom_symbols:
        symbols = [s.strip().upper().replace(".NS", "") for s in custom_symbols.split(",") if s.strip()]
        u_name = f"Custom ({len(symbols)} symbols)"
    else:
        symbols = get_universe_symbols(universe)
        u_labels = {
            "nifty50": "Nifty 50 Index Universe",
            "nifty100": "Nifty 100 Index Universe",
            "full": "Full NSE Filtered Universe (>₹1,000 Cr Mcap)",
            "sample": "Sample Universe (Top 20 Stocks)",
        }
        u_name = u_labels.get(universe, f"{universe} Universe")

    if not symbols:
        return jsonify({"status": "error", "message": "No symbols available to scan."}), 400

    period_clean = period.lower().replace("yr", "y")

    # LIVE-PARITY: always load 5y of daily context like the live scanner
    # (--history 5y default); the analysis window is enforced by the period
    # argument passed to run_historical_backtest.
    sym_data = load_batch_history(symbols, period_years=5, max_workers=4)

    # Run backtest
    res = run_historical_backtest(
        symbols_data=sym_data,
        touch_filter=touch_filter,
        period=period_clean,
        universe_name=u_name,
        target_pct=target_pct,
        stop_pct=stop_pct,
        max_hold_days=max_hold,
    )

    # Generate PDF
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_t = touch_filter.replace("%", "pct").replace(" ", "_").lower()
    pdf_filename = f"fibo_backtest_{clean_t}_{period_clean}_{ts}.pdf"
    pdf_path = REPORTS_DIR / pdf_filename
    build_pdf_report(res, pdf_path)

    # Store latest state
    CURRENT_STATE["result"] = res.to_dict()
    CURRENT_STATE["pdf_filename"] = pdf_filename
    CURRENT_STATE["last_updated"] = datetime.now().isoformat()

    return jsonify({
        "status": "success",
        "result": res.to_dict(),
        "pdf_filename": pdf_filename,
    })


@app.route("/api/initial-data")
def api_initial_data():
    """Return precomputed or cached backtest state for fast first render."""
    if CURRENT_STATE.get("result"):
        return jsonify({
            "result": CURRENT_STATE["result"],
            "pdf_filename": CURRENT_STATE["pdf_filename"],
        })

    # Generate initial default 2y Nifty 50 backtest (5y context, live-parity)
    syms = get_universe_symbols("nifty50", limit=10)
    data = load_batch_history(syms, period_years=5, max_workers=4)
    res = run_historical_backtest(data, touch_filter="both", period="2y", universe_name="Nifty 50 Universe")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_name = f"fibo_backtest_both_2y_{ts}.pdf"
    pdf_path = REPORTS_DIR / pdf_name
    build_pdf_report(res, pdf_path)

    CURRENT_STATE["result"] = res.to_dict()
    CURRENT_STATE["pdf_filename"] = pdf_name

    return jsonify({
        "result": res.to_dict(),
        "pdf_filename": pdf_name,
    })


@app.route("/reports/<path:filename>")
def serve_report(filename):
    if filename == "latest.pdf":
        if CURRENT_STATE.get("pdf_filename"):
            filename = CURRENT_STATE["pdf_filename"]
        else:
            files = sorted(REPORTS_DIR.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                filename = files[0].name
            else:
                return "No reports generated yet.", 404

    target = (REPORTS_DIR / filename).resolve()
    if not target.exists() or not str(target).startswith(str(REPORTS_DIR.resolve())):
        return "Report not found", 404
    return send_file(target, mimetype="application/pdf")


def run_server(port: int = 5000, host: str = "0.0.0.0"):
    log.info("Starting FIBO Backtest Dashboard on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    run_server(port=port)

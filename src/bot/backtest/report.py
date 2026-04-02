"""HTML report generator for backtest results.

Produces a single self-contained HTML file with Chart.js charts,
a dark trading-dashboard theme, and all metrics / trade log tables.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bot.backtest.models import (
    BacktestResult,
    FullBacktestReport,
    MonteCarloResult,
    PerformanceMetrics,
    WalkForwardResult,
)

logger = logging.getLogger(__name__)

# ── Colour palette ──────────────────────────────────────────────────────────
_BG = "#1a1a2e"
_SURFACE = "#16213e"
_CARD = "#0f3460"
_BORDER = "#1a4a6e"
_TEXT = "#e0e0e0"
_MUTED = "#8892a4"
_ACCENT = "#00d4aa"
_LOSS = "#ff4757"
_WARN = "#ffa502"
_CHART_LINE = "#00d4aa"
_CHART_DD = "#ff4757"


class ReportGenerator:
    """Generates a self-contained HTML backtest report.

    Usage::

        gen = ReportGenerator()
        html = gen.generate(full_report)
        path = gen.save(full_report, "/tmp/report.html")
    """

    # ── Public API ──────────────────────────────────────────────────────────

    def generate(self, report: FullBacktestReport) -> str:
        """Render a full backtest report as an HTML string.

        Args:
            report: The FullBacktestReport produced by BacktestEngine.

        Returns:
            A self-contained HTML document as a string.
        """
        bt = report.backtest
        cfg = bt.config

        title = f"{cfg.strategy} / {cfg.asset} Backtest Report"
        date_range = (
            f"{cfg.start_date.strftime('%Y-%m-%d')} "
            f"to {cfg.end_date.strftime('%Y-%m-%d')}"
        )
        run_ms = f"{bt.run_duration_ms:.0f} ms"

        sections: list[str] = [
            self._render_header(title, date_range, run_ms),
            self._render_metrics_grid(bt.metrics, cfg.initial_bankroll),
            self._render_equity_chart(bt),
            self._render_drawdown_chart(bt),
            self._render_trade_log(bt),
        ]

        if report.walk_forward is not None:
            sections.append(self._render_walk_forward(report.walk_forward))

        if report.monte_carlo is not None:
            sections.append(self._render_monte_carlo(report.monte_carlo))

        body = "\n".join(sections)
        return self._wrap_html(title, body)

    def save(self, report: FullBacktestReport, path: str) -> str:
        """Render and save the HTML report to disk.

        Args:
            report: The FullBacktestReport to render.
            path: Absolute or relative filesystem path to write the HTML file.

        Returns:
            The resolved absolute path that was written.
        """
        html = self.generate(report)
        resolved = Path(path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(html, encoding="utf-8")
        logger.info("Backtest report saved to %s", resolved)
        return str(resolved)

    # ── Section renderers ───────────────────────────────────────────────────

    def _render_header(
        self, title: str, date_range: str, run_ms: str
    ) -> str:
        return f"""
<section class="header-bar">
  <div class="header-title">{title}</div>
  <div class="header-meta">
    <span class="badge">{date_range}</span>
    <span class="badge muted">Run: {run_ms}</span>
  </div>
</section>
"""

    def _render_metrics_grid(
        self, m: PerformanceMetrics, initial_bankroll: float
    ) -> str:
        win_rate_pct = f"{m.win_rate * 100:.1f}%"
        pnl_cls = "profit" if m.total_pnl >= 0 else "loss"
        pf_cls = "profit" if m.profit_factor >= 1.0 else "loss"
        sharpe_cls = "profit" if m.sharpe_ratio >= 0.5 else (
            "warn" if m.sharpe_ratio >= 0 else "loss"
        )
        roi = (m.final_bankroll - initial_bankroll) / initial_bankroll * 100

        def cell(label: str, value: str, cls: str = "") -> str:
            span = f'<span class="metric-value {cls}">{value}</span>' if cls else \
                   f'<span class="metric-value">{value}</span>'
            return (
                f'<div class="metric-cell">'
                f'<div class="metric-label">{label}</div>'
                f'{span}'
                f'</div>'
            )

        return f"""
<section class="card">
  <h2 class="section-title">Performance Summary</h2>
  <div class="metrics-grid">
    {cell("Total Trades", str(m.total_trades))}
    {cell("Wins", str(m.wins), "profit")}
    {cell("Losses", str(m.losses), "loss")}
    {cell("Win Rate", win_rate_pct, "profit" if m.win_rate >= 0.5 else "loss")}
    {cell("Total P&amp;L", f"${m.total_pnl:+.4f}", pnl_cls)}
    {cell("ROI", f"{roi:+.2f}%", pnl_cls)}
    {cell("Final Bankroll", f"${m.final_bankroll:.4f}")}
    {cell("Avg P&amp;L / Trade", f"${m.avg_pnl_per_trade:+.4f}", pnl_cls)}
    {cell("Gross Profit", f"${m.gross_profit:.4f}", "profit")}
    {cell("Gross Loss", f"${m.gross_loss:.4f}", "loss")}
    {cell("Profit Factor", f"{m.profit_factor:.4f}", pf_cls)}
    {cell("Expectancy", f"${m.expectancy:+.4f}", pnl_cls)}
    {cell("Sharpe Ratio", f"{m.sharpe_ratio:.4f}", sharpe_cls)}
    {cell("Sortino Ratio", f"{m.sortino_ratio:.4f}", sharpe_cls)}
    {cell("Max Drawdown", f"${m.max_drawdown:.4f}", "loss")}
    {cell("Max Drawdown %", f"{m.max_drawdown_pct * 100:.2f}%", "loss")}
    {cell("Recovery Factor", f"{m.recovery_factor:.4f}")}
    {cell("Avg Win", f"${m.avg_win:.4f}", "profit")}
    {cell("Avg Loss", f"${m.avg_loss:.4f}", "loss")}
    {cell("Max Consec. Wins", str(m.max_consecutive_wins), "profit")}
    {cell("Max Consec. Losses", str(m.max_consecutive_losses), "loss")}
  </div>
</section>
"""

    def _render_equity_chart(self, bt: BacktestResult) -> str:
        if not bt.equity_curve:
            return ""

        # Build timestamp labels (one per trade + initial point)
        labels: list[str] = ["Start"]
        for ts in bt.timestamps:
            labels.append(ts.strftime("%Y-%m-%d %H:%M"))

        # Pad labels if needed
        while len(labels) < len(bt.equity_curve):
            labels.append(labels[-1])

        labels_json = json.dumps(labels[: len(bt.equity_curve)])
        equity_json = json.dumps([round(v, 4) for v in bt.equity_curve])

        initial = bt.equity_curve[0] if bt.equity_curve else 0
        final = bt.equity_curve[-1] if bt.equity_curve else 0
        pnl_colour = _ACCENT if final >= initial else _LOSS

        return f"""
<section class="card">
  <h2 class="section-title">Equity Curve</h2>
  <div class="chart-container">
    <canvas id="equityChart"></canvas>
  </div>
</section>
<script>
(function() {{
  var ctx = document.getElementById('equityChart').getContext('2d');
  var labels = {labels_json};
  var data = {equity_json};
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        label: 'Bankroll (USDC)',
        data: data,
        borderColor: '{pnl_colour}',
        backgroundColor: '{pnl_colour}18',
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        fill: true,
        tension: 0.2,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '{_TEXT}' }} }},
        tooltip: {{
          backgroundColor: '{_CARD}',
          titleColor: '{_TEXT}',
          bodyColor: '{_MUTED}',
          borderColor: '{_BORDER}',
          borderWidth: 1,
        }},
      }},
      scales: {{
        x: {{
          ticks: {{
            color: '{_MUTED}',
            maxTicksLimit: 12,
            maxRotation: 30,
          }},
          grid: {{ color: '{_BORDER}40' }},
        }},
        y: {{
          ticks: {{
            color: '{_MUTED}',
            callback: function(v) {{ return '$' + v.toFixed(2); }},
          }},
          grid: {{ color: '{_BORDER}40' }},
        }},
      }},
    }},
  }});
}})();
</script>
"""

    def _render_drawdown_chart(self, bt: BacktestResult) -> str:
        if not bt.drawdown_curve:
            return ""

        labels: list[str] = ["Start"]
        for ts in bt.timestamps:
            labels.append(ts.strftime("%Y-%m-%d %H:%M"))

        while len(labels) < len(bt.drawdown_curve):
            labels.append(labels[-1])

        labels_json = json.dumps(labels[: len(bt.drawdown_curve)])
        dd_pct = [round(v * 100, 4) for v in bt.drawdown_curve]
        dd_json = json.dumps(dd_pct)

        return f"""
<section class="card">
  <h2 class="section-title">Drawdown (%)</h2>
  <div class="chart-container">
    <canvas id="drawdownChart"></canvas>
  </div>
</section>
<script>
(function() {{
  var ctx = document.getElementById('drawdownChart').getContext('2d');
  var labels = {labels_json};
  var data = {dd_json};
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: labels,
      datasets: [{{
        label: 'Drawdown (%)',
        data: data,
        borderColor: '{_LOSS}',
        backgroundColor: '{_LOSS}28',
        borderWidth: 1.5,
        pointRadius: 0,
        pointHoverRadius: 4,
        fill: true,
        tension: 0.1,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{ labels: {{ color: '{_TEXT}' }} }},
        tooltip: {{
          backgroundColor: '{_CARD}',
          titleColor: '{_TEXT}',
          bodyColor: '{_MUTED}',
          borderColor: '{_BORDER}',
          borderWidth: 1,
        }},
      }},
      scales: {{
        x: {{
          ticks: {{
            color: '{_MUTED}',
            maxTicksLimit: 12,
            maxRotation: 30,
          }},
          grid: {{ color: '{_BORDER}40' }},
        }},
        y: {{
          reverse: true,
          ticks: {{
            color: '{_MUTED}',
            callback: function(v) {{ return '-' + v.toFixed(1) + '%'; }},
          }},
          grid: {{ color: '{_BORDER}40' }},
        }},
      }},
    }},
  }});
}})();
</script>
"""

    def _render_trade_log(self, bt: BacktestResult) -> str:
        if not bt.trades:
            return """
<section class="card">
  <h2 class="section-title">Trade Log</h2>
  <p class="muted">No trades recorded.</p>
</section>
"""
        rows: list[str] = []
        for i, t in enumerate(bt.trades):
            outcome_cls = "profit" if t.exit_outcome == "WIN" else "loss"
            pnl_cls = "profit" if t.pnl >= 0 else "loss"
            rows.append(
                f"<tr>"
                f"<td>{i + 1}</td>"
                f"<td>{t.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</td>"
                f"<td>{t.signal}</td>"
                f"<td>{t.entry_price:.4f}</td>"
                f"<td class='{outcome_cls}'>{t.exit_outcome}</td>"
                f"<td>{t.bet_size:.4f}</td>"
                f"<td class='{pnl_cls}'>{t.pnl:+.4f}</td>"
                f"<td>{t.confidence:.3f}</td>"
                f"<td>{t.bankroll_after:.4f}</td>"
                f"</tr>"
            )

        rows_html = "\n".join(rows)
        return f"""
<section class="card">
  <h2 class="section-title">Trade Log ({len(bt.trades)} trades)</h2>
  <div class="table-scroll">
    <table class="data-table" id="tradeLogTable">
      <thead>
        <tr>
          <th>#</th>
          <th>Timestamp</th>
          <th>Signal</th>
          <th>Entry</th>
          <th>Outcome</th>
          <th>Bet Size</th>
          <th>P&amp;L</th>
          <th>Confidence</th>
          <th>Bankroll After</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
</section>
"""

    def _render_walk_forward(self, wf: WalkForwardResult) -> str:
        overfitting_cls = (
            "profit" if wf.overfitting_score >= 0.7
            else ("warn" if wf.overfitting_score >= 0.4 else "loss")
        )
        overfitting_label = (
            "Good" if wf.overfitting_score >= 0.7
            else ("Moderate" if wf.overfitting_score >= 0.4 else "High Overfit")
        )

        rows: list[str] = []
        for w in wf.windows:
            is_m = w.in_sample
            oos_m = w.out_of_sample
            oos_wr_cls = "profit" if oos_m.win_rate >= 0.5 else "loss"
            oos_pnl_cls = "profit" if oos_m.total_pnl >= 0 else "loss"
            rows.append(
                f"<tr>"
                f"<td>{w.window_index + 1}</td>"
                f"<td>{w.train_start.strftime('%Y-%m-%d')}</td>"
                f"<td>{w.train_end.strftime('%Y-%m-%d')}</td>"
                f"<td>{w.test_start.strftime('%Y-%m-%d')}</td>"
                f"<td>{w.test_end.strftime('%Y-%m-%d')}</td>"
                f"<td>{is_m.win_rate * 100:.1f}%</td>"
                f"<td>{is_m.sharpe_ratio:.3f}</td>"
                f"<td class='{oos_wr_cls}'>{oos_m.win_rate * 100:.1f}%</td>"
                f"<td>{oos_m.sharpe_ratio:.3f}</td>"
                f"<td class='{oos_pnl_cls}'>{oos_m.total_pnl:+.4f}</td>"
                f"<td>{oos_m.total_trades}</td>"
                f"</tr>"
            )

        rows_html = "\n".join(rows)

        agg = wf.aggregated_oos
        agg_pnl_cls = "profit" if agg.total_pnl >= 0 else "loss"

        return f"""
<section class="card">
  <h2 class="section-title">Walk-Forward Analysis
    <span class="badge {overfitting_cls}" style="font-size:0.75rem;margin-left:12px;">
      Overfitting Score: {wf.overfitting_score:.3f} &mdash; {overfitting_label}
    </span>
  </h2>
  <div class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>Window</th>
          <th>Train Start</th>
          <th>Train End</th>
          <th>Test Start</th>
          <th>Test End</th>
          <th>IS Win Rate</th>
          <th>IS Sharpe</th>
          <th>OOS Win Rate</th>
          <th>OOS Sharpe</th>
          <th>OOS P&amp;L</th>
          <th>OOS Trades</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
  <div class="metrics-grid" style="margin-top:1.5rem;">
    <div class="metric-cell">
      <div class="metric-label">Aggregated OOS Win Rate</div>
      <span class="metric-value {'profit' if agg.win_rate >= 0.5 else 'loss'}">{agg.win_rate * 100:.1f}%</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Aggregated OOS Sharpe</div>
      <span class="metric-value">{agg.sharpe_ratio:.4f}</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Aggregated OOS P&amp;L</div>
      <span class="metric-value {agg_pnl_cls}">${agg.total_pnl:+.4f}</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Aggregated OOS Trades</div>
      <span class="metric-value">{agg.total_trades}</span>
    </div>
  </div>
</section>
"""

    def _render_monte_carlo(self, mc: MonteCarloResult) -> str:
        prob_profit_pct = f"{mc.prob_profit * 100:.1f}%"
        prob_ruin_pct = f"{mc.prob_ruin * 100:.1f}%"
        profit_cls = "profit" if mc.prob_profit >= 0.6 else (
            "warn" if mc.prob_profit >= 0.4 else "loss"
        )
        ruin_cls = "loss" if mc.prob_ruin > 0.1 else (
            "warn" if mc.prob_ruin > 0.05 else "profit"
        )

        def mc_cell(label: str, value: str, cls: str = "") -> str:
            span = f'<span class="metric-value {cls}">{value}</span>' if cls else \
                   f'<span class="metric-value">{value}</span>'
            return (
                f'<div class="metric-cell">'
                f'<div class="metric-label">{label}</div>'
                f'{span}'
                f'</div>'
            )

        return f"""
<section class="card">
  <h2 class="section-title">Monte Carlo Analysis
    <span class="badge muted" style="font-size:0.75rem;margin-left:12px;">
      {mc.iterations:,} iterations &mdash; {mc.confidence_level * 100:.0f}% confidence
    </span>
  </h2>
  <div class="mc-columns">
    <div>
      <h3 class="subsection-title">Final Equity Distribution</h3>
      <div class="metrics-grid">
        {mc_cell("Median Final Equity", f"${mc.median_final_equity:.4f}")}
        {mc_cell("5th Percentile (Worst)", f"${mc.p5_final_equity:.4f}", "loss")}
        {mc_cell("95th Percentile (Best)", f"${mc.p95_final_equity:.4f}", "profit")}
      </div>
    </div>
    <div>
      <h3 class="subsection-title">Drawdown Distribution</h3>
      <div class="metrics-grid">
        {mc_cell("Median Max Drawdown", f"${mc.median_max_drawdown:.4f}", "warn")}
        {mc_cell("95th Pct Max Drawdown", f"${mc.p95_max_drawdown:.4f}", "loss")}
      </div>
    </div>
    <div>
      <h3 class="subsection-title">Sharpe Distribution</h3>
      <div class="metrics-grid">
        {mc_cell("Median Sharpe", f"{mc.median_sharpe:.4f}")}
        {mc_cell("5th Pct Sharpe", f"{mc.p5_sharpe:.4f}", "loss")}
        {mc_cell("95th Pct Sharpe", f"{mc.p95_sharpe:.4f}", "profit")}
      </div>
    </div>
  </div>
  <div class="probability-bar">
    <div class="prob-item">
      <div class="prob-label">Probability of Profit</div>
      <div class="prob-value {profit_cls}">{prob_profit_pct}</div>
      <div class="prob-bar-track">
        <div class="prob-bar-fill profit" style="width:{min(mc.prob_profit * 100, 100):.1f}%"></div>
      </div>
    </div>
    <div class="prob-item">
      <div class="prob-label">Probability of Ruin (&lt;10% bankroll)</div>
      <div class="prob-value {ruin_cls}">{prob_ruin_pct}</div>
      <div class="prob-bar-track">
        <div class="prob-bar-fill loss" style="width:{min(mc.prob_ruin * 100, 100):.1f}%"></div>
      </div>
    </div>
  </div>
</section>
"""

    # ── HTML shell ───────────────────────────────────────────────────────────

    def _wrap_html(self, title: str, body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title}</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: {_BG};
      color: {_TEXT};
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      font-size: 14px;
      line-height: 1.6;
      min-height: 100vh;
      padding: 0 0 40px;
    }}

    /* ── Header ── */
    .header-bar {{
      background: linear-gradient(135deg, {_SURFACE} 0%, {_CARD} 100%);
      border-bottom: 2px solid {_ACCENT};
      padding: 20px 32px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }}
    .header-title {{
      font-size: 1.5rem;
      font-weight: 700;
      color: {_ACCENT};
      letter-spacing: 0.02em;
    }}
    .header-meta {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}

    /* ── Badges ── */
    .badge {{
      background: {_CARD};
      border: 1px solid {_BORDER};
      border-radius: 6px;
      padding: 4px 10px;
      font-size: 0.8rem;
      color: {_TEXT};
    }}
    .badge.muted {{ color: {_MUTED}; }}
    .badge.profit {{ color: {_ACCENT}; border-color: {_ACCENT}40; }}
    .badge.warn   {{ color: {_WARN};   border-color: {_WARN}40; }}
    .badge.loss   {{ color: {_LOSS};   border-color: {_LOSS}40; }}

    /* ── Cards ── */
    .card {{
      background: {_SURFACE};
      border: 1px solid {_BORDER};
      border-radius: 12px;
      padding: 24px 28px;
      margin: 20px 28px 0;
    }}
    .section-title {{
      font-size: 1.05rem;
      font-weight: 600;
      color: {_TEXT};
      margin-bottom: 18px;
      padding-bottom: 10px;
      border-bottom: 1px solid {_BORDER};
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .subsection-title {{
      font-size: 0.9rem;
      font-weight: 600;
      color: {_MUTED};
      margin-bottom: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}

    /* ── Metrics grid ── */
    .metrics-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 12px;
    }}
    .metric-cell {{
      background: {_CARD};
      border: 1px solid {_BORDER};
      border-radius: 8px;
      padding: 12px 14px;
    }}
    .metric-label {{
      font-size: 0.72rem;
      color: {_MUTED};
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 6px;
    }}
    .metric-value {{
      font-size: 1.05rem;
      font-weight: 600;
      color: {_TEXT};
    }}
    .metric-value.profit {{ color: {_ACCENT}; }}
    .metric-value.loss   {{ color: {_LOSS}; }}
    .metric-value.warn   {{ color: {_WARN}; }}

    /* ── Charts ── */
    .chart-container {{
      position: relative;
      height: 280px;
      width: 100%;
    }}

    /* ── Tables ── */
    .table-scroll {{
      overflow-x: auto;
      border-radius: 8px;
      border: 1px solid {_BORDER};
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }}
    .data-table thead tr {{
      background: {_CARD};
    }}
    .data-table th {{
      padding: 10px 12px;
      text-align: left;
      color: {_MUTED};
      font-weight: 600;
      text-transform: uppercase;
      font-size: 0.7rem;
      letter-spacing: 0.06em;
      border-bottom: 1px solid {_BORDER};
      white-space: nowrap;
    }}
    .data-table td {{
      padding: 8px 12px;
      border-bottom: 1px solid {_BORDER}80;
      color: {_TEXT};
      white-space: nowrap;
    }}
    .data-table tbody tr:hover {{
      background: {_CARD}60;
    }}
    .data-table tbody tr:last-child td {{
      border-bottom: none;
    }}
    td.profit {{ color: {_ACCENT}; font-weight: 600; }}
    td.loss   {{ color: {_LOSS};   font-weight: 600; }}
    td.warn   {{ color: {_WARN};   font-weight: 600; }}

    /* ── Walk-forward ── */
    /* inherits card + data-table styles */

    /* ── Monte Carlo ── */
    .mc-columns {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 24px;
      margin-bottom: 24px;
    }}
    .probability-bar {{
      display: flex;
      flex-direction: column;
      gap: 16px;
    }}
    .prob-item {{
      display: grid;
      grid-template-columns: 240px 80px 1fr;
      align-items: center;
      gap: 12px;
    }}
    .prob-label {{
      font-size: 0.85rem;
      color: {_MUTED};
    }}
    .prob-value {{
      font-size: 1rem;
      font-weight: 700;
      text-align: right;
    }}
    .prob-value.profit {{ color: {_ACCENT}; }}
    .prob-value.loss   {{ color: {_LOSS}; }}
    .prob-value.warn   {{ color: {_WARN}; }}
    .prob-bar-track {{
      background: {_CARD};
      border-radius: 4px;
      height: 10px;
      overflow: hidden;
      border: 1px solid {_BORDER};
    }}
    .prob-bar-fill {{
      height: 100%;
      border-radius: 4px;
      transition: width 0.3s ease;
    }}
    .prob-bar-fill.profit {{ background: {_ACCENT}; }}
    .prob-bar-fill.loss   {{ background: {_LOSS}; }}

    /* ── Misc ── */
    .muted {{ color: {_MUTED}; }}
    p.muted {{ padding: 8px 0; }}

    @media (max-width: 600px) {{
      .card {{ margin: 12px 10px 0; padding: 16px; }}
      .header-bar {{ padding: 16px; }}
      .metrics-grid {{ grid-template-columns: repeat(2, 1fr); }}
      .prob-item {{ grid-template-columns: 1fr 60px; }}
      .prob-bar-track {{ display: none; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""

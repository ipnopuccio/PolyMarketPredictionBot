"""HTML comparison report for multi-strategy evaluation.

Generates a self-contained HTML page with:
  - Ranking table with composite scores
  - Side-by-side metrics comparison
  - Sharpe ratio bar chart
  - Chi-square significance test results
  - Walk-forward & Monte Carlo summaries
  - Equity curve overlay chart
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from bot.backtest.comparison import ComparisonResult, StrategyScore
from bot.backtest.evaluator import EvaluationResult
from bot.backtest.models import FullBacktestReport

logger = logging.getLogger(__name__)

# ── Colour palette (matches report.py) ─────────────────────────────────────
_BG = "#1a1a2e"
_SURFACE = "#16213e"
_CARD = "#0f3460"
_BORDER = "#1a4a6e"
_TEXT = "#e0e0e0"
_MUTED = "#8892a4"
_ACCENT = "#00d4aa"
_LOSS = "#ff4757"
_WARN = "#ffa502"

# Per-strategy colours for charts
_STRATEGY_COLOURS = [
    "#00d4aa", "#3498db", "#f1c40f", "#e74c3c", "#9b59b6",
    "#1abc9c", "#e67e22", "#2ecc71", "#e84393", "#0984e3",
]


class ComparisonReportGenerator:
    """Generate a side-by-side HTML comparison report."""

    def generate(
        self,
        eval_result: EvaluationResult,
        comparison: ComparisonResult,
    ) -> str:
        """Render the comparison report as HTML.

        Args:
            eval_result: The full evaluation output with per-strategy reports.
            comparison: Statistical comparison with ranked scores.

        Returns:
            Self-contained HTML string.
        """
        title = "Strategy Evaluation Report"

        sections = [
            self._render_header(title, eval_result),
            self._render_ranking_table(comparison),
            self._render_sharpe_chart(comparison),
            self._render_equity_overlay(eval_result),
            self._render_chi_square(comparison),
            self._render_detailed_cards(comparison),
        ]

        body = "\n".join(sections)
        return self._wrap_html(title, body)

    def save(
        self,
        eval_result: EvaluationResult,
        comparison: ComparisonResult,
        path: str,
    ) -> str:
        """Render and save the HTML report to disk."""
        html = self.generate(eval_result, comparison)
        resolved = Path(path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(html, encoding="utf-8")
        logger.info("Comparison report saved to %s", resolved)
        return str(resolved)

    # ── Section renderers ──────────────────────────────────────────────────

    def _render_header(self, title: str, eval_result: EvaluationResult) -> str:
        n = len(eval_result.reports)
        ms = f"{eval_result.run_duration_ms:.0f} ms"
        return f"""
<section class="header-bar">
  <div class="header-title">{title}</div>
  <div class="header-meta">
    <span class="badge">{n} strategies evaluated</span>
    <span class="badge muted">Runtime: {ms}</span>
  </div>
</section>
"""

    def _render_ranking_table(self, comp: ComparisonResult) -> str:
        rows: list[str] = []
        for s in comp.scores:
            pnl_cls = "profit" if s.total_pnl >= 0 else "loss"
            wr_cls = "profit" if s.win_rate >= 0.5 else "loss"
            sharpe_cls = "profit" if s.sharpe >= 0.5 else ("warn" if s.sharpe >= 0 else "loss")
            rank_badge = "rank-gold" if s.rank == 1 else ("rank-silver" if s.rank == 2 else "rank-bronze" if s.rank == 3 else "")

            mc_pp = f"{s.mc_prob_profit:.0%}" if s.mc_prob_profit is not None else "—"
            wf_of = f"{s.wf_overfitting_score:.2f}" if s.wf_overfitting_score is not None else "—"

            rows.append(
                f"<tr>"
                f"<td><span class='rank-badge {rank_badge}'>#{s.rank}</span></td>"
                f"<td><strong>{s.strategy}</strong></td>"
                f"<td>{s.asset}</td>"
                f"<td><strong>{s.composite_score:.4f}</strong></td>"
                f"<td class='{sharpe_cls}'>{s.sharpe:.4f}</td>"
                f"<td>{s.sortino:.4f}</td>"
                f"<td class='{wr_cls}'>{s.win_rate:.1%}</td>"
                f"<td>{s.win_rate_ci_low:.1%}–{s.win_rate_ci_high:.1%}</td>"
                f"<td class='{pnl_cls}'>${s.total_pnl:+.4f}</td>"
                f"<td>{s.profit_factor:.2f}</td>"
                f"<td class='loss'>{s.max_drawdown_pct:.1%}</td>"
                f"<td>{s.total_trades}</td>"
                f"<td>{mc_pp}</td>"
                f"<td>{wf_of}</td>"
                f"</tr>"
            )

        rows_html = "\n".join(rows)
        return f"""
<section class="card">
  <h2 class="section-title">Strategy Ranking (by Composite Score)</h2>
  <div class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th>Rank</th><th>Strategy</th><th>Asset</th><th>Score</th>
          <th>Sharpe</th><th>Sortino</th><th>Win Rate</th><th>WR 95% CI</th>
          <th>Total P&amp;L</th><th>PF</th><th>Max DD</th><th>Trades</th>
          <th>MC P(Profit)</th><th>WF Overfit</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
</section>
"""

    def _render_sharpe_chart(self, comp: ComparisonResult) -> str:
        labels = [f"{s.strategy}/{s.asset}" for s in comp.scores]
        values = [s.sharpe for s in comp.scores]
        colours = [_STRATEGY_COLOURS[i % len(_STRATEGY_COLOURS)] for i in range(len(comp.scores))]

        return f"""
<section class="card">
  <h2 class="section-title">Sharpe Ratio Comparison</h2>
  <div class="chart-container">
    <canvas id="sharpeChart"></canvas>
  </div>
</section>
<script>
(function() {{
  var ctx = document.getElementById('sharpeChart').getContext('2d');
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: {json.dumps(labels)},
      datasets: [{{
        label: 'Sharpe Ratio',
        data: {json.dumps(values)},
        backgroundColor: {json.dumps(colours)},
        borderColor: {json.dumps(colours)},
        borderWidth: 1,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{
          backgroundColor: '{_CARD}',
          titleColor: '{_TEXT}',
          bodyColor: '{_MUTED}',
        }},
      }},
      scales: {{
        x: {{
          ticks: {{ color: '{_MUTED}' }},
          grid: {{ color: '{_BORDER}40' }},
          title: {{ display: true, text: 'Sharpe Ratio', color: '{_MUTED}' }},
        }},
        y: {{
          ticks: {{ color: '{_TEXT}', font: {{ weight: '600' }} }},
          grid: {{ display: false }},
        }},
      }},
    }},
  }});
}})();
</script>
"""

    def _render_equity_overlay(self, eval_result: EvaluationResult) -> str:
        datasets: list[str] = []
        for i, report in enumerate(eval_result.reports):
            bt = report.backtest
            label = f"{bt.config.strategy}/{bt.config.asset}"
            colour = _STRATEGY_COLOURS[i % len(_STRATEGY_COLOURS)]
            data = json.dumps([round(v, 4) for v in bt.equity_curve])
            datasets.append(f"""{{
        label: '{label}',
        data: {data},
        borderColor: '{colour}',
        backgroundColor: '{colour}18',
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        tension: 0.2,
      }}""")

        # Use longest equity curve length for labels
        max_len = max((len(r.backtest.equity_curve) for r in eval_result.reports), default=0)
        labels = json.dumps(list(range(max_len)))

        return f"""
<section class="card">
  <h2 class="section-title">Equity Curves Overlay</h2>
  <div class="chart-container" style="height:320px;">
    <canvas id="equityOverlay"></canvas>
  </div>
</section>
<script>
(function() {{
  var ctx = document.getElementById('equityOverlay').getContext('2d');
  new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: {labels},
      datasets: [{','.join(datasets)}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'index', intersect: false }},
      plugins: {{
        legend: {{
          labels: {{ color: '{_TEXT}', usePointStyle: true, padding: 16 }},
          position: 'top',
        }},
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
          display: false,
        }},
        y: {{
          ticks: {{
            color: '{_MUTED}',
            callback: function(v) {{ return '$' + v.toFixed(2); }},
          }},
          grid: {{ color: '{_BORDER}40' }},
          title: {{ display: true, text: 'Bankroll (USDC)', color: '{_MUTED}' }},
        }},
      }},
    }},
  }});
}})();
</script>
"""

    def _render_chi_square(self, comp: ComparisonResult) -> str:
        verdict_cls = "loss" if comp.chi_square_significant else "profit"
        verdict_text = (
            "SIGNIFICANT — win rates differ across strategies (reject H0)"
            if comp.chi_square_significant
            else "NOT SIGNIFICANT — no evidence of win rate differences (fail to reject H0)"
        )

        return f"""
<section class="card">
  <h2 class="section-title">Chi-Square Test: Win Rate Homogeneity</h2>
  <p style="margin-bottom:16px;color:{_MUTED};">
    H0: All strategies share the same underlying win rate.<br/>
    H1: At least one strategy has a significantly different win rate.
  </p>
  <div class="metrics-grid">
    <div class="metric-cell">
      <div class="metric-label">Chi-Square Statistic</div>
      <span class="metric-value">{comp.chi_square_statistic:.4f}</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Degrees of Freedom</div>
      <span class="metric-value">{comp.chi_square_df}</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Critical Value (alpha=0.05)</div>
      <span class="metric-value">{comp.chi_square_critical:.3f}</span>
    </div>
    <div class="metric-cell">
      <div class="metric-label">Verdict</div>
      <span class="metric-value {verdict_cls}">{verdict_text}</span>
    </div>
  </div>
</section>
"""

    def _render_detailed_cards(self, comp: ComparisonResult) -> str:
        cards: list[str] = []
        for s in comp.scores:
            pnl_cls = "profit" if s.total_pnl >= 0 else "loss"
            sharpe_cls = "profit" if s.sharpe >= 0.5 else ("warn" if s.sharpe >= 0 else "loss")

            mc_section = ""
            if s.mc_prob_profit is not None:
                mc_pp_cls = "profit" if s.mc_prob_profit >= 0.6 else ("warn" if s.mc_prob_profit >= 0.4 else "loss")
                mc_ruin_cls = "loss" if (s.mc_prob_ruin or 0) > 0.1 else "profit"
                mc_section = f"""
      <div class="detail-row">
        <span class="detail-label">MC Prob Profit</span>
        <span class="detail-value {mc_pp_cls}">{s.mc_prob_profit:.1%}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">MC Prob Ruin</span>
        <span class="detail-value {mc_ruin_cls}">{s.mc_prob_ruin:.1%}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">MC Median Sharpe</span>
        <span class="detail-value">{s.mc_median_sharpe:.4f}</span>
      </div>"""

            wf_section = ""
            if s.wf_overfitting_score is not None:
                wf_cls = "profit" if s.wf_overfitting_score < 2.0 else "loss"
                wf_section = f"""
      <div class="detail-row">
        <span class="detail-label">WF Overfit Score</span>
        <span class="detail-value {wf_cls}">{s.wf_overfitting_score:.3f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">WF OOS Sharpe</span>
        <span class="detail-value">{s.wf_oos_sharpe:.4f}</span>
      </div>"""

            cards.append(f"""
    <div class="strategy-card">
      <div class="strategy-card-header">
        <span class="rank-badge {'rank-gold' if s.rank == 1 else 'rank-silver' if s.rank == 2 else 'rank-bronze' if s.rank == 3 else ''}">#{s.rank}</span>
        <strong>{s.strategy} / {s.asset}</strong>
      </div>
      <div class="detail-row">
        <span class="detail-label">Composite Score</span>
        <span class="detail-value"><strong>{s.composite_score:.4f}</strong></span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Sharpe</span>
        <span class="detail-value {sharpe_cls}">{s.sharpe:.4f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Win Rate</span>
        <span class="detail-value">{s.win_rate:.1%} [{s.win_rate_ci_low:.1%}–{s.win_rate_ci_high:.1%}]</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Total P&amp;L</span>
        <span class="detail-value {pnl_cls}">${s.total_pnl:+.4f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">P&amp;L/Trade CI</span>
        <span class="detail-value">${s.pnl_per_trade_ci_low:+.4f} to ${s.pnl_per_trade_ci_high:+.4f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Profit Factor</span>
        <span class="detail-value">{s.profit_factor:.2f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Expectancy</span>
        <span class="detail-value">${s.expectancy:+.4f}</span>
      </div>
      <div class="detail-row">
        <span class="detail-label">Max Drawdown</span>
        <span class="detail-value loss">{s.max_drawdown_pct:.1%}</span>
      </div>{mc_section}{wf_section}
    </div>""")

        return f"""
<section class="card">
  <h2 class="section-title">Detailed Strategy Cards</h2>
  <div class="strategy-grid">
    {''.join(cards)}
  </div>
</section>
"""

    # ── HTML shell ──────────────────────────────────────────────────────────

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

    .header-bar {{
      background: linear-gradient(135deg, {_SURFACE} 0%, {_CARD} 100%);
      border-bottom: 2px solid {_ACCENT};
      padding: 20px 32px;
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 12px;
    }}
    .header-title {{ font-size: 1.5rem; font-weight: 700; color: {_ACCENT}; }}
    .header-meta {{ display: flex; gap: 10px; flex-wrap: wrap; }}

    .badge {{
      background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 6px;
      padding: 4px 10px; font-size: 0.8rem; color: {_TEXT};
    }}
    .badge.muted {{ color: {_MUTED}; }}
    .badge.profit {{ color: {_ACCENT}; border-color: {_ACCENT}40; }}
    .badge.warn {{ color: {_WARN}; border-color: {_WARN}40; }}
    .badge.loss {{ color: {_LOSS}; border-color: {_LOSS}40; }}

    .card {{
      background: {_SURFACE}; border: 1px solid {_BORDER}; border-radius: 12px;
      padding: 24px 28px; margin: 20px 28px 0;
    }}
    .section-title {{
      font-size: 1.05rem; font-weight: 600; color: {_TEXT};
      margin-bottom: 18px; padding-bottom: 10px;
      border-bottom: 1px solid {_BORDER};
    }}

    .metrics-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px;
    }}
    .metric-cell {{
      background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 8px; padding: 12px 14px;
    }}
    .metric-label {{
      font-size: 0.72rem; color: {_MUTED}; text-transform: uppercase;
      letter-spacing: 0.06em; margin-bottom: 6px;
    }}
    .metric-value {{ font-size: 1.05rem; font-weight: 600; color: {_TEXT}; }}
    .metric-value.profit {{ color: {_ACCENT}; }}
    .metric-value.loss {{ color: {_LOSS}; }}
    .metric-value.warn {{ color: {_WARN}; }}

    .chart-container {{ position: relative; height: 280px; width: 100%; }}

    .table-scroll {{
      overflow-x: auto; border-radius: 8px; border: 1px solid {_BORDER};
    }}
    .data-table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
    .data-table thead tr {{ background: {_CARD}; }}
    .data-table th {{
      padding: 10px 12px; text-align: left; color: {_MUTED}; font-weight: 600;
      text-transform: uppercase; font-size: 0.7rem; letter-spacing: 0.06em;
      border-bottom: 1px solid {_BORDER}; white-space: nowrap;
    }}
    .data-table td {{
      padding: 8px 12px; border-bottom: 1px solid {_BORDER}80;
      color: {_TEXT}; white-space: nowrap;
    }}
    .data-table tbody tr:hover {{ background: {_CARD}60; }}
    td.profit, .profit {{ color: {_ACCENT}; font-weight: 600; }}
    td.loss, .loss {{ color: {_LOSS}; font-weight: 600; }}
    td.warn, .warn {{ color: {_WARN}; font-weight: 600; }}

    .rank-badge {{
      display: inline-block; width: 28px; text-align: center;
      font-weight: 700; font-size: 0.85rem; color: {_MUTED};
    }}
    .rank-gold {{ color: #ffd700; }}
    .rank-silver {{ color: #c0c0c0; }}
    .rank-bronze {{ color: #cd7f32; }}

    .strategy-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px;
    }}
    .strategy-card {{
      background: {_CARD}; border: 1px solid {_BORDER}; border-radius: 10px;
      padding: 16px 18px;
    }}
    .strategy-card-header {{
      font-size: 1rem; margin-bottom: 12px; padding-bottom: 8px;
      border-bottom: 1px solid {_BORDER}; display: flex; align-items: center; gap: 8px;
    }}
    .detail-row {{
      display: flex; justify-content: space-between; padding: 3px 0;
      font-size: 0.85rem;
    }}
    .detail-label {{ color: {_MUTED}; }}
    .detail-value {{ font-weight: 600; }}

    @media (max-width: 600px) {{
      .card {{ margin: 12px 10px 0; padding: 16px; }}
      .header-bar {{ padding: 16px; }}
      .strategy-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""

"""
Comprehensive Report Generator for Complete Backtest Validation.

Generates detailed HTML reports with:
- All UQSS levels comparison
- Three-stage test results
- Regime analysis
- Clear recommendations
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


def _to_python_float(value: Any) -> Union[float, int]:
    """Convert numpy/pandas numeric types to Python float for JSON serialization."""
    if hasattr(value, "item"):
        # numpy scalar types have .item() method
        return value.item()
    return float(value) if value is not None else 0.0


class ComprehensiveReportGenerator:
    """
    Generate comprehensive HTML reports from complete backtest validation.

    Covers all aspects of strategy validation with actionable recommendations.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT:USDT",
        period: str = "1y",
    ):
        """
        Initialize comprehensive report generator.

        Args:
            symbol: Trading symbol
            period: Backtest period
        """
        self.symbol = symbol
        self.period = period
        self.levels_results: List[Dict[str, Any]] = []
        self.three_stage_results: Optional[Dict[str, Any]] = None
        self.regime_results: Optional[Dict[str, Any]] = None
        self.simple_regime_results: Optional[Dict[str, Any]] = None
        self.btc_benchmark: float = 0.0
        self.data_info: Dict[str, Any] = {}
        self.funding_info: Dict[str, Any] = {}

    def set_data_info(
        self,
        start_date: datetime,
        end_date: datetime,
        total_bars: int,
        btc_return: float,
    ):
        """Set data information."""
        self.data_info = {
            "start_date": start_date,
            "end_date": end_date,
            "total_bars": total_bars,
        }
        self.btc_benchmark = btc_return

    def set_funding_info(self, count: int, avg_rate: float):
        """Set funding rate information."""
        self.funding_info = {
            "count": count,
            "avg_rate": avg_rate,
        }

    def add_level_result(self, level: int, name: str, metrics: Dict[str, Any]):
        """Add a UQSS level backtest result."""
        self.levels_results.append({
            "level": level,
            "name": name,
            "metrics": metrics,
        })

    def set_three_stage_results(self, results: Dict[str, Any]):
        """Set three-stage validation results."""
        self.three_stage_results = results

    def set_regime_results(
        self,
        detailed_results: Dict[str, Any],
        simple_results: Optional[Dict[str, Any]] = None,
    ):
        """Set regime analysis results."""
        self.regime_results = detailed_results
        self.simple_regime_results = simple_results

    def generate(self) -> str:
        """Generate comprehensive HTML report."""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>完整回测验证报告 - {self.symbol}</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        {self._generate_css()}
    </style>
</head>
<body>
    {self._generate_header()}
    <main class="container">
        {self._generate_summary_section()}
        {self._generate_levels_section()}
        {self._generate_three_stage_section()}
        {self._generate_regime_section()}
        {self._generate_recommendations_section()}
    </main>
    {self._generate_footer()}
</body>
</html>"""
        return html

    def save(self, filepath: Path) -> None:
        """Save report to file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        html = self.generate()
        filepath.write_text(html, encoding="utf-8")

    def _generate_css(self) -> str:
        """Generate CSS styles."""
        return """
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background-color: #f5f7fa;
            color: #2c3e50;
            line-height: 1.6;
        }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white;
            padding: 40px 20px;
            text-align: center;
        }
        header h1 { font-size: 2.5em; margin-bottom: 10px; }
        header .subtitle { opacity: 0.9; font-size: 1.1em; }
        .section {
            background: white;
            border-radius: 12px;
            padding: 25px;
            margin: 25px 0;
            box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        }
        .section h2 {
            color: #1a1a2e;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 3px solid #0f3460;
            font-size: 1.5em;
        }
        .section h3 {
            color: #16213e;
            margin: 20px 0 15px;
            font-size: 1.2em;
        }

        /* Summary Cards */
        .summary-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .summary-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 10px;
            padding: 20px;
        }
        .summary-card.success { background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); }
        .summary-card.warning { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .summary-card.info { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
        .summary-card .title { font-size: 0.9em; opacity: 0.9; margin-bottom: 5px; }
        .summary-card .value { font-size: 2em; font-weight: bold; }
        .summary-card .detail { font-size: 0.85em; opacity: 0.8; margin-top: 5px; }

        /* Metrics Grid */
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 15px;
        }
        .metric-card {
            background: #f8f9fa;
            border-radius: 8px;
            padding: 15px;
            text-align: center;
            border-left: 4px solid #667eea;
        }
        .metric-card .value { font-size: 1.5em; font-weight: bold; color: #2c3e50; }
        .metric-card .label { font-size: 0.85em; color: #6c757d; margin-top: 5px; }
        .metric-card.positive { border-left-color: #28a745; }
        .metric-card.positive .value { color: #28a745; }
        .metric-card.negative { border-left-color: #dc3545; }
        .metric-card.negative .value { color: #dc3545; }

        /* Tables */
        table { width: 100%; border-collapse: collapse; margin-top: 15px; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #e9ecef; }
        th { background: #f8f9fa; font-weight: 600; color: #495057; }
        tr:hover { background: #f8f9fa; }
        .profit { color: #28a745; font-weight: 600; }
        .loss { color: #dc3545; font-weight: 600; }

        /* Stage Cards */
        .stage-card {
            border: 2px solid #e9ecef;
            border-radius: 10px;
            padding: 20px;
            margin: 15px 0;
        }
        .stage-card.pass { border-color: #28a745; background: #f8fff9; }
        .stage-card.fail { border-color: #dc3545; background: #fff8f8; }
        .stage-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .stage-title { font-size: 1.2em; font-weight: 600; }
        .stage-status {
            padding: 5px 15px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.9em;
        }
        .stage-status.pass { background: #28a745; color: white; }
        .stage-status.fail { background: #dc3545; color: white; }

        /* Recommendations */
        .recommendation {
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 15px 20px;
            margin: 10px 0;
            border-radius: 0 8px 8px 0;
        }
        .recommendation.success {
            background: #d4edda;
            border-left-color: #28a745;
        }
        .recommendation.danger {
            background: #f8d7da;
            border-left-color: #dc3545;
        }
        .recommendation.info {
            background: #cce5ff;
            border-left-color: #0d6efd;
        }
        .recommendation-title {
            font-weight: 600;
            margin-bottom: 5px;
        }

        /* Charts */
        .chart-container { width: 100%; height: 350px; margin: 15px 0; }

        /* Best Badge */
        .best-badge {
            background: #ffc107;
            color: #000;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 0.75em;
            font-weight: 600;
            margin-left: 10px;
        }

        footer {
            text-align: center;
            padding: 30px;
            color: #6c757d;
            font-size: 0.9em;
        }
        """

    def _generate_header(self) -> str:
        """Generate header section."""
        start = self.data_info.get("start_date", datetime.now())
        end = self.data_info.get("end_date", datetime.now())
        bars = self.data_info.get("total_bars", 0)

        return f"""
        <header>
            <h1>完整回测验证报告</h1>
            <div class="subtitle">
                {self.symbol} | 周期: {self.period} |
                {start.strftime('%Y-%m-%d') if hasattr(start, 'strftime') else start} 至
                {end.strftime('%Y-%m-%d') if hasattr(end, 'strftime') else end} |
                {bars:,} 数据条
            </div>
        </header>
        """

    def _generate_summary_section(self) -> str:
        """Generate executive summary section."""
        # Find best level - convert sharpe to Python float for comparison
        best_level = None
        best_sharpe = -999.0
        for lr in self.levels_results:
            sharpe = _to_python_float(lr["metrics"].get("sharpe_ratio", 0))
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_level = lr

        # Three-stage overall result
        three_stage_pass = False
        if self.three_stage_results:
            three_stage_pass = self.three_stage_results.get("summary", {}).get("all_pass", False)

        overall_status = "success" if three_stage_pass else "warning"
        overall_text = "策略验证通过" if three_stage_pass else "策略需要改进"

        # Convert benchmark and funding values to Python float
        btc_benchmark = _to_python_float(self.btc_benchmark)
        avg_funding_rate = _to_python_float(self.funding_info.get('avg_rate', 0))

        return f"""
        <section class="section">
            <h2>执行摘要 (Executive Summary)</h2>
            <div class="summary-grid">
                <div class="summary-card {overall_status}">
                    <div class="title">整体评估</div>
                    <div class="value">{overall_text}</div>
                    <div class="detail">三阶段验证{'全部通过' if three_stage_pass else '部分未通过'}</div>
                </div>
                <div class="summary-card info">
                    <div class="title">BTC 基准收益</div>
                    <div class="value">{btc_benchmark:+.1f}%</div>
                    <div class="detail">同期买入持有收益</div>
                </div>
                <div class="summary-card">
                    <div class="title">最佳策略级别</div>
                    <div class="value">L{best_level['level'] if best_level else '?'}</div>
                    <div class="detail">{best_level['name'] if best_level else 'N/A'} (Sharpe: {best_sharpe:.2f})</div>
                </div>
                <div class="summary-card info">
                    <div class="title">资金费率</div>
                    <div class="value">{self.funding_info.get('count', 0)} 条</div>
                    <div class="detail">平均 {avg_funding_rate:.4f}% / 8h</div>
                </div>
            </div>
        </section>
        """

    def _generate_levels_section(self) -> str:
        """Generate UQSS levels comparison section."""
        if not self.levels_results:
            return ""

        # Sort by level
        sorted_levels = sorted(self.levels_results, key=lambda x: x["level"])

        # Find best by Sharpe - convert to Python float
        best_sharpe = _to_python_float(max(_to_python_float(lr["metrics"].get("sharpe_ratio", -999)) for lr in sorted_levels))

        rows = ""
        for lr in sorted_levels:
            m = lr["metrics"]
            ret = _to_python_float(m.get("total_return_pct", 0))
            sharpe = _to_python_float(m.get("sharpe_ratio", 0))
            dd = _to_python_float(m.get("max_drawdown_pct", 0))
            wr = _to_python_float(m.get("win_rate_pct", 0))
            trades = int(m.get("total_trades", 0))
            is_best = sharpe == _to_python_float(best_sharpe) and sharpe > 0

            ret_class = "profit" if ret > 0 else "loss" if ret < 0 else ""
            sharpe_class = "profit" if sharpe > 1 else "loss" if sharpe < 0 else ""

            badge = '<span class="best-badge">BEST</span>' if is_best else ""

            rows += f"""
            <tr>
                <td><strong>L{lr['level']}</strong> {lr['name']}{badge}</td>
                <td class="{ret_class}">{ret:+.2f}%</td>
                <td class="{sharpe_class}">{sharpe:.2f}</td>
                <td>{dd:.2f}%</td>
                <td>{wr:.1f}%</td>
                <td>{trades}</td>
            </tr>
            """

        # Level chart data - convert to JSON-safe format
        levels = [f"L{lr['level']}" for lr in sorted_levels]
        returns = [_to_python_float(lr["metrics"].get("total_return_pct", 0)) for lr in sorted_levels]
        sharpes = [_to_python_float(lr["metrics"].get("sharpe_ratio", 0)) for lr in sorted_levels]

        # Convert to JSON strings for safe JavaScript embedding
        levels_json = json.dumps(levels)
        returns_json = json.dumps(returns)
        sharpes_json = json.dumps(sharpes)

        return f"""
        <section class="section">
            <h2>UQSS 策略级别对比</h2>
            <p style="color: #6c757d; margin-bottom: 15px;">
                UQSS (Universal Quant Stratification Standard) 基于时间视界和阿尔法属性分类策略
            </p>
            <table>
                <thead>
                    <tr>
                        <th>级别</th>
                        <th>收益率</th>
                        <th>夏普比率</th>
                        <th>最大回撤</th>
                        <th>胜率</th>
                        <th>交易次数</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>

            <div id="levels-chart" class="chart-container"></div>
            <script>
                var levelsData = [
                    {{
                        x: {levels_json},
                        y: {returns_json},
                        type: 'bar',
                        name: '收益率 (%)',
                        marker: {{ color: '#667eea' }}
                    }},
                    {{
                        x: {levels_json},
                        y: {sharpes_json},
                        type: 'scatter',
                        mode: 'lines+markers',
                        name: '夏普比率',
                        yaxis: 'y2',
                        line: {{ color: '#f5576c', width: 3 }},
                        marker: {{ size: 10 }}
                    }}
                ];
                var levelsLayout = {{
                    margin: {{ t: 30, r: 60, b: 40, l: 60 }},
                    xaxis: {{ title: 'UQSS 级别' }},
                    yaxis: {{ title: '收益率 (%)' }},
                    yaxis2: {{
                        title: '夏普比率',
                        overlaying: 'y',
                        side: 'right'
                    }},
                    legend: {{ x: 0, y: 1.1, orientation: 'h' }},
                    hovermode: 'x unified'
                }};
                Plotly.newPlot('levels-chart', levelsData, levelsLayout, {{responsive: true}});
            </script>
        </section>
        """

    def _generate_three_stage_section(self) -> str:
        """Generate three-stage validation section."""
        if not self.three_stage_results:
            return ""

        s1 = self.three_stage_results.get("stage1", {})
        s2 = self.three_stage_results.get("stage2", {})
        s3 = self.three_stage_results.get("stage3", {})
        summary = self.three_stage_results.get("summary", {})

        # Stage status
        s1_pass = summary.get("stage1_pass", False)
        s2_pass = summary.get("stage2_pass", False)
        s3_pass = summary.get("stage3_pass", False)

        def stage_card(title: str, passed: bool, content: str) -> str:
            status = "pass" if passed else "fail"
            status_text = "通过" if passed else "未通过"
            return f"""
            <div class="stage-card {status}">
                <div class="stage-header">
                    <div class="stage-title">{title}</div>
                    <div class="stage-status {status}">{status_text}</div>
                </div>
                <div class="stage-content">{content}</div>
            </div>
            """

        # Stage 1 metrics - convert to Python floats
        s1_sharpe = _to_python_float(s1.get('in_sample_sharpe', 0))
        s1_return = _to_python_float(s1.get('in_sample_return', 0))
        s1_drawdown = _to_python_float(s1.get('in_sample_drawdown', 0))
        s1_trades = int(s1.get('in_sample_trades', 0))

        stage1_content = f"""
        <div class="metrics-grid">
            <div class="metric-card {'positive' if s1_sharpe >= 1 else 'negative'}">
                <div class="value">{s1_sharpe:.2f}</div>
                <div class="label">样本内夏普</div>
            </div>
            <div class="metric-card {'positive' if s1_return > 0 else 'negative'}">
                <div class="value">{s1_return:+.2f}%</div>
                <div class="label">样本内收益</div>
            </div>
            <div class="metric-card">
                <div class="value">{s1_drawdown:.2f}%</div>
                <div class="label">最大回撤</div>
            </div>
            <div class="metric-card">
                <div class="value">{s1_trades}</div>
                <div class="label">交易次数</div>
            </div>
        </div>
        <p style="margin-top: 10px; color: #6c757d;">
            最优参数: {s1.get('best_params', {})}
        </p>
        """

        # Stage 2 metrics - convert to Python floats
        robustness = _to_python_float(s2.get("robustness_ratio", 0))
        pos_windows = int(s2.get("positive_windows", 0))
        total_windows = int(s2.get("windows_count", 1))
        avg_train_return = _to_python_float(s2.get('avg_train_return', 0))
        avg_test_return = _to_python_float(s2.get('avg_test_return', 0))

        stage2_content = f"""
        <div class="metrics-grid">
            <div class="metric-card {'positive' if robustness >= 0.5 else 'negative'}">
                <div class="value">{robustness:.2f}</div>
                <div class="label">鲁棒性比率</div>
            </div>
            <div class="metric-card {'positive' if pos_windows/max(total_windows,1) >= 0.5 else 'negative'}">
                <div class="value">{pos_windows}/{total_windows}</div>
                <div class="label">正收益窗口</div>
            </div>
            <div class="metric-card">
                <div class="value">{avg_train_return:.2f}%</div>
                <div class="label">平均训练收益</div>
            </div>
            <div class="metric-card">
                <div class="value">{avg_test_return:.2f}%</div>
                <div class="label">平均测试收益</div>
            </div>
        </div>
        """

        # Stage 3 metrics - convert to Python floats
        s3_sharpe = _to_python_float(s3.get('holdout_sharpe', 0))
        s3_return = _to_python_float(s3.get('holdout_return', 0))
        s3_drawdown = _to_python_float(s3.get('holdout_drawdown', 0))
        degradation = _to_python_float(summary.get("degradation", 0))

        stage3_content = f"""
        <div class="metrics-grid">
            <div class="metric-card {'positive' if s3_sharpe >= 0.5 else 'negative'}">
                <div class="value">{s3_sharpe:.2f}</div>
                <div class="label">样本外夏普</div>
            </div>
            <div class="metric-card {'positive' if s3_return > 0 else 'negative'}">
                <div class="value">{s3_return:+.2f}%</div>
                <div class="label">样本外收益</div>
            </div>
            <div class="metric-card">
                <div class="value">{s3_drawdown:.2f}%</div>
                <div class="label">最大回撤</div>
            </div>
            <div class="metric-card {'positive' if degradation <= 0.5 else 'negative'}">
                <div class="value">{degradation:.0%}</div>
                <div class="label">性能衰减</div>
            </div>
        </div>
        """

        return f"""
        <section class="section">
            <h2>三阶段验证结果</h2>
            <p style="color: #6c757d; margin-bottom: 15px;">
                完整的策略验证流程：样本内优化 → 滚动验证 → 样本外测试
            </p>

            {stage_card("阶段 1: 样本内优化 (In-Sample Optimization)", s1_pass, stage1_content)}
            {stage_card("阶段 2: 滚动验证 (Walk-Forward Validation)", s2_pass, stage2_content)}
            {stage_card("阶段 3: 样本外测试 (Holdout Test)", s3_pass, stage3_content)}
        </section>
        """

    def _generate_regime_section(self) -> str:
        """Generate regime analysis section."""
        if not self.regime_results:
            return ""

        regime_summary = self.regime_results.get("summary", {})
        regime_perf = self.regime_results.get("performance", {})

        # Regime distribution chart - convert to JSON-safe format
        regimes = list(regime_summary.keys())
        pcts = [_to_python_float(regime_summary.get(r, 0)) for r in regimes]
        regime_labels = [r.replace('_pct', '').replace('_', ' ').title() for r in regimes]

        # Convert to JSON for safe JavaScript embedding
        labels_json = json.dumps(regime_labels)
        pcts_json = json.dumps(pcts)

        # Performance table
        perf_rows = ""
        for regime, perf in regime_perf.items():
            ret = _to_python_float(perf.get("return_pct", 0))
            count = perf.get("count", 0)
            ret_class = "profit" if ret > 0 else "loss" if ret < 0 else ""
            perf_rows += f"""
            <tr>
                <td>{regime}</td>
                <td class="{ret_class}">{ret:+.2f}%</td>
                <td>{count}</td>
            </tr>
            """

        # Simple regime section
        simple_section = ""
        if self.simple_regime_results:
            sr = self.simple_regime_results
            bull_pct = _to_python_float(sr.get('bull_pct', 0))
            bear_pct = _to_python_float(sr.get('bear_pct', 0))
            ranging_pct = _to_python_float(sr.get('ranging_pct', 0))
            simple_section = f"""
            <h3>简化市场状态分析 (US-10 规格)</h3>
            <p style="color: #6c757d; margin-bottom: 15px;">
                牛市: 上涨 > 20% | 熊市: 下跌 > 20% | 震荡: 其他
            </p>
            <div class="metrics-grid">
                <div class="metric-card positive">
                    <div class="value">{bull_pct:.1f}%</div>
                    <div class="label">牛市时段</div>
                </div>
                <div class="metric-card negative">
                    <div class="value">{bear_pct:.1f}%</div>
                    <div class="label">熊市时段</div>
                </div>
                <div class="metric-card">
                    <div class="value">{ranging_pct:.1f}%</div>
                    <div class="label">震荡时段</div>
                </div>
            </div>
            """

        return f"""
        <section class="section">
            <h2>市场状态分析 (Regime Analysis)</h2>

            <h3>详细市场状态分布</h3>
            <div id="regime-chart" class="chart-container" style="height: 300px;"></div>
            <script>
                var regimeData = [{{
                    labels: {labels_json},
                    values: {pcts_json},
                    type: 'pie',
                    hole: 0.4,
                    marker: {{
                        colors: ['#28a745', '#dc3545', '#6c757d', '#ffc107']
                    }}
                }}];
                var regimeLayout = {{
                    margin: {{ t: 30, r: 30, b: 30, l: 30 }},
                    showlegend: true
                }};
                Plotly.newPlot('regime-chart', regimeData, regimeLayout, {{responsive: true}});
            </script>

            <h3>各状态策略表现</h3>
            <table>
                <thead>
                    <tr>
                        <th>市场状态</th>
                        <th>收益率</th>
                        <th>数据条数</th>
                    </tr>
                </thead>
                <tbody>
                    {perf_rows}
                </tbody>
            </table>

            {simple_section}
        </section>
        """

    def _generate_recommendations_section(self) -> str:
        """Generate recommendations section."""
        recommendations = []

        # Analyze three-stage results
        if self.three_stage_results:
            summary = self.three_stage_results.get("summary", {})

            if not summary.get("stage1_pass"):
                recommendations.append({
                    "type": "danger",
                    "title": "样本内优化未达标",
                    "content": "样本内夏普比率 < 1.0，建议：1) 调整策略逻辑；2) 尝试不同的参数范围；3) 考虑其他技术指标组合"
                })

            if not summary.get("stage2_pass"):
                robustness = _to_python_float(summary.get("robustness_ratio", 0))
                recommendations.append({
                    "type": "warning",
                    "title": f"滚动验证鲁棒性不足 ({robustness:.2f})",
                    "content": "参数在时间序列上不稳定，建议：1) 使用更长的回测周期；2) 增加参数约束；3) 考虑自适应参数"
                })

            if not summary.get("stage3_pass"):
                degradation = _to_python_float(summary.get("degradation", 0))
                recommendations.append({
                    "type": "danger",
                    "title": f"样本外性能衰减严重 ({degradation:.0%})",
                    "content": "可能存在过拟合，建议：1) 减少优化参数数量；2) 使用更保守的参数；3) 增加样本外数据比例"
                })

            if summary.get("all_pass"):
                recommendations.append({
                    "type": "success",
                    "title": "三阶段验证全部通过",
                    "content": "策略具备较好的稳健性，可以进入模拟盘测试阶段。建议使用 10% 资金进行 2-4 周的实盘验证。"
                })

        # Analyze levels - convert sharpe to Python float for comparison
        if self.levels_results:
            positive_levels = [lr for lr in self.levels_results if _to_python_float(lr["metrics"].get("sharpe_ratio", 0)) > 0]
            if len(positive_levels) == 0:
                recommendations.append({
                    "type": "danger",
                    "title": "所有级别夏普比率为负",
                    "content": "策略在当前市场条件下表现不佳，建议：1) 分析是否处于不适合的市场状态；2) 考虑做空策略；3) 重新审视策略假设"
                })
            elif len(positive_levels) >= 3:
                recommendations.append({
                    "type": "success",
                    "title": f"{len(positive_levels)} 个级别夏普比率为正",
                    "content": "策略在多个时间视界下表现良好，表明核心逻辑有效。建议选择夏普比率最高且回撤可接受的级别。"
                })

        # Regime recommendations - convert return_pct to Python float for comparison
        if self.regime_results:
            regime_perf = self.regime_results.get("performance", {})
            negative_regimes = [r for r, p in regime_perf.items() if _to_python_float(p.get("return_pct", 0)) < -5]
            if negative_regimes:
                recommendations.append({
                    "type": "info",
                    "title": "特定市场状态表现不佳",
                    "content": f"在 {', '.join(negative_regimes)} 状态下亏损较大。建议：1) 在该状态下减少仓位；2) 添加状态过滤条件"
                })

        # General recommendations - convert btc_benchmark to Python float
        btc_benchmark = _to_python_float(self.btc_benchmark)
        if btc_benchmark < -20:
            recommendations.append({
                "type": "info",
                "title": "回测期间为熊市",
                "content": f"BTC 同期下跌 {btc_benchmark:.1f}%，策略在熊市中的表现是重要参考。如果策略亏损小于 BTC，说明具有一定的防御能力。"
            })

        if not recommendations:
            recommendations.append({
                "type": "info",
                "title": "暂无特别建议",
                "content": "请结合具体情况分析回测结果。"
            })

        recs_html = ""
        for rec in recommendations:
            recs_html += f"""
            <div class="recommendation {rec['type']}">
                <div class="recommendation-title">{rec['title']}</div>
                <div>{rec['content']}</div>
            </div>
            """

        return f"""
        <section class="section">
            <h2>建议与下一步行动</h2>
            {recs_html}

            <h3 style="margin-top: 25px;">后续步骤</h3>
            <ol style="margin-left: 20px; line-height: 2;">
                <li>如果三阶段验证通过：进入模拟盘测试 (1-4 周)</li>
                <li>如果部分阶段未通过：根据上述建议调整策略</li>
                <li>模拟盘验证通过后：使用 10% 资金进行小规模实盘测试</li>
                <li>实盘测试稳定后：逐步增加资金 (30% → 50% → 100%)</li>
            </ol>
        </section>
        """

    def _generate_footer(self) -> str:
        """Generate footer section."""
        return f"""
        <footer>
            <p>QuantForge 完整回测验证报告 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>本报告仅供参考，不构成投资建议</p>
        </footer>
        """

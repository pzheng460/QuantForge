"""
Report generation for backtest results.

Generates interactive HTML reports with:
- Performance metrics summary
- Equity curve visualization
- Drawdown analysis
- Trade list and statistics
"""

from pathlib import Path


from nexustrader.backtest.result import BacktestResult


class ReportGenerator:
    """
    Generate HTML reports from backtest results.

    Creates interactive reports with charts and metrics.
    """

    def __init__(self, result: BacktestResult):
        """
        Initialize report generator.

        Args:
            result: Backtest result to report on
        """
        self.result = result

    def generate(self) -> str:
        """
        Generate HTML report.

        Returns:
            HTML string
        """
        # Build report sections
        header = self._generate_header()
        metrics_section = self._generate_metrics_section()
        equity_section = self._generate_equity_section()
        drawdown_section = self._generate_drawdown_section()
        trades_section = self._generate_trades_section()
        footer = self._generate_footer()

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Backtest Report - {self.result.config.symbol}</title>
    <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
    <style>
        {self._generate_css()}
    </style>
</head>
<body>
    {header}
    <main class="container">
        {metrics_section}
        {equity_section}
        {drawdown_section}
        {trades_section}
    </main>
    {footer}
</body>
</html>"""

        return html

    def save(self, filepath: Path) -> None:
        """
        Save report to file.

        Args:
            filepath: Path to save HTML file
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        html = self.generate()
        filepath.write_text(html)

    def _generate_css(self) -> str:
        """Generate CSS styles."""
        return """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background-color: #f5f5f5;
            color: #333;
            line-height: 1.6;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
        }
        header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
        }
        header h1 {
            font-size: 2em;
            margin-bottom: 10px;
        }
        header .subtitle {
            opacity: 0.9;
        }
        .section {
            background: white;
            border-radius: 8px;
            padding: 20px;
            margin: 20px 0;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .section h2 {
            color: #667eea;
            margin-bottom: 15px;
            padding-bottom: 10px;
            border-bottom: 2px solid #f0f0f0;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }
        .metric-card {
            background: #f8f9fa;
            border-radius: 6px;
            padding: 15px;
            text-align: center;
        }
        .metric-card .value {
            font-size: 1.8em;
            font-weight: bold;
            color: #333;
        }
        .metric-card .label {
            font-size: 0.9em;
            color: #666;
            margin-top: 5px;
        }
        .metric-card.positive .value { color: #28a745; }
        .metric-card.negative .value { color: #dc3545; }
        .chart-container {
            width: 100%;
            height: 400px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #eee;
        }
        th {
            background: #f8f9fa;
            font-weight: 600;
        }
        tr:hover {
            background: #f8f9fa;
        }
        .profit { color: #28a745; }
        .loss { color: #dc3545; }
        footer {
            text-align: center;
            padding: 20px;
            color: #666;
            font-size: 0.9em;
        }
        """

    def _generate_header(self) -> str:
        """Generate header section."""
        config = self.result.config
        return f"""
        <header>
            <h1>Backtest Report</h1>
            <div class="subtitle">
                {config.symbol} | {config.interval.value} |
                {config.start_date.strftime('%Y-%m-%d')} to {config.end_date.strftime('%Y-%m-%d')}
            </div>
        </header>
        """

    def _generate_metrics_section(self) -> str:
        """Generate metrics section."""
        metrics = self.result.metrics

        def format_metric(value: float, suffix: str = "", precision: int = 2) -> str:
            if isinstance(value, (int, float)):
                return f"{value:.{precision}f}{suffix}"
            return str(value)

        def get_class(value: float) -> str:
            if value > 0:
                return "positive"
            elif value < 0:
                return "negative"
            return ""

        total_return = metrics.get("total_return_pct", 0)
        max_dd = metrics.get("max_drawdown_pct", 0)
        sharpe = metrics.get("sharpe_ratio", 0)
        sortino = metrics.get("sortino_ratio", 0)
        win_rate = metrics.get("win_rate_pct", 0)
        profit_factor = metrics.get("profit_factor", 0)
        total_trades = metrics.get("total_trades", 0)

        return f"""
        <section class="section">
            <h2>Performance Summary</h2>
            <div class="metrics-grid">
                <div class="metric-card {get_class(total_return)}">
                    <div class="value">{format_metric(total_return, '%')}</div>
                    <div class="label">Total Return</div>
                </div>
                <div class="metric-card negative">
                    <div class="value">{format_metric(max_dd, '%')}</div>
                    <div class="label">Max Drawdown</div>
                </div>
                <div class="metric-card {get_class(sharpe)}">
                    <div class="value">{format_metric(sharpe)}</div>
                    <div class="label">Sharpe Ratio</div>
                </div>
                <div class="metric-card {get_class(sortino)}">
                    <div class="value">{format_metric(sortino)}</div>
                    <div class="label">Sortino Ratio</div>
                </div>
                <div class="metric-card">
                    <div class="value">{format_metric(win_rate, '%')}</div>
                    <div class="label">Win Rate</div>
                </div>
                <div class="metric-card">
                    <div class="value">{format_metric(profit_factor)}</div>
                    <div class="label">Profit Factor</div>
                </div>
                <div class="metric-card">
                    <div class="value">{int(total_trades)}</div>
                    <div class="label">Total Trades</div>
                </div>
            </div>
        </section>
        """

    def _generate_equity_section(self) -> str:
        """Generate equity curve section."""
        equity = self.result.equity_curve

        # Prepare data for Plotly
        timestamps = [t.isoformat() for t in equity.index]
        values = equity.values.tolist()

        return f"""
        <section class="section">
            <h2>Equity Curve</h2>
            <div id="equity-chart" class="chart-container"></div>
            <script>
                var equityData = [{{
                    x: {timestamps},
                    y: {values},
                    type: 'scatter',
                    mode: 'lines',
                    name: 'Equity',
                    line: {{ color: '#667eea', width: 2 }}
                }}];
                var equityLayout = {{
                    margin: {{ t: 20, r: 20, b: 40, l: 60 }},
                    xaxis: {{ title: 'Time' }},
                    yaxis: {{ title: 'Equity' }},
                    hovermode: 'x unified'
                }};
                Plotly.newPlot('equity-chart', equityData, equityLayout, {{responsive: true}});
            </script>
        </section>
        """

    def _generate_drawdown_section(self) -> str:
        """Generate drawdown section."""
        equity = self.result.equity_curve

        # Calculate drawdown
        rolling_max = equity.cummax()
        drawdown = (equity - rolling_max) / rolling_max * 100

        timestamps = [t.isoformat() for t in drawdown.index]
        values = drawdown.values.tolist()

        return f"""
        <section class="section">
            <h2>Drawdown</h2>
            <div id="drawdown-chart" class="chart-container"></div>
            <script>
                var drawdownData = [{{
                    x: {timestamps},
                    y: {values},
                    type: 'scatter',
                    mode: 'lines',
                    fill: 'tozeroy',
                    name: 'Drawdown',
                    line: {{ color: '#dc3545', width: 1 }},
                    fillcolor: 'rgba(220, 53, 69, 0.3)'
                }}];
                var drawdownLayout = {{
                    margin: {{ t: 20, r: 20, b: 40, l: 60 }},
                    xaxis: {{ title: 'Time' }},
                    yaxis: {{ title: 'Drawdown (%)', autorange: 'reversed' }},
                    hovermode: 'x unified'
                }};
                Plotly.newPlot('drawdown-chart', drawdownData, drawdownLayout, {{responsive: true}});
            </script>
        </section>
        """

    def _generate_trades_section(self) -> str:
        """Generate trades section."""
        trades = self.result.trades

        if not trades:
            return """
            <section class="section">
                <h2>Trades</h2>
                <p>No trades executed during this backtest.</p>
            </section>
            """

        # Filter to closing trades (those with PnL)
        closing_trades = [t for t in trades if t.pnl != 0]

        if not closing_trades:
            rows = ""
            for trade in trades[:20]:  # Show first 20 trades
                rows += f"""
                <tr>
                    <td>{trade.timestamp.strftime('%Y-%m-%d %H:%M')}</td>
                    <td>{trade.side.upper()}</td>
                    <td>{trade.price:.2f}</td>
                    <td>{trade.amount:.6f}</td>
                    <td>{trade.fee:.4f}</td>
                    <td>-</td>
                </tr>
                """
        else:
            rows = ""
            for trade in closing_trades[:20]:  # Show first 20 closing trades
                pnl_class = "profit" if trade.pnl > 0 else "loss"
                rows += f"""
                <tr>
                    <td>{trade.timestamp.strftime('%Y-%m-%d %H:%M')}</td>
                    <td>{trade.side.upper()}</td>
                    <td>{trade.price:.2f}</td>
                    <td>{trade.amount:.6f}</td>
                    <td>{trade.fee:.4f}</td>
                    <td class="{pnl_class}">{trade.pnl:+.2f} ({trade.pnl_pct:+.2f}%)</td>
                </tr>
                """

        return f"""
        <section class="section">
            <h2>Trades</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Side</th>
                        <th>Price</th>
                        <th>Amount</th>
                        <th>Fee</th>
                        <th>PnL</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
            {f'<p style="margin-top: 10px; color: #666;">Showing {min(20, len(closing_trades or trades))} of {len(closing_trades or trades)} trades</p>' if len(closing_trades or trades) > 20 else ''}
        </section>
        """

    def _generate_footer(self) -> str:
        """Generate footer section."""
        return f"""
        <footer>
            <p>Generated by NexusTrader Backtest Engine | {self.result.run_time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Backtest Duration: {self.result.duration_seconds:.2f}s</p>
        </footer>
        """

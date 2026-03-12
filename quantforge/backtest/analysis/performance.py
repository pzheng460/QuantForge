"""
Performance analysis for backtest results.

Calculates comprehensive trading performance metrics including:
- Return metrics (total, annualized)
- Risk metrics (Sharpe, Sortino, Calmar, max drawdown)
- Trade statistics (win rate, profit factor, expectancy)
- Time-based analysis (daily, monthly returns)
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from quantforge.backtest.result import TradeRecord


def infer_periods_per_year(index: pd.DatetimeIndex) -> int:
    """Infer annualisation factor from a DatetimeIndex.

    Computes the median bar duration from the index and converts it to
    the number of bars per year, so Sharpe / Sortino / Calmar ratios
    are correct for any bar interval (15m, 1h, 4h, 1d, …).

    Falls back to 35 040 (15-minute bars) when the index has fewer than
    2 elements or yields a non-positive duration.
    """
    _FALLBACK = 4 * 24 * 365  # 15-min bars
    if len(index) < 2:
        return _FALLBACK
    diffs = pd.Series(index.asi8).diff().dropna()
    median_ns = diffs.median()
    if median_ns <= 0:
        return _FALLBACK
    median_seconds = median_ns / 1e9
    return max(1, round(365.25 * 86400 / median_seconds))


class PerformanceAnalyzer:
    """
    Analyze backtest performance and calculate metrics.

    Provides comprehensive analysis of:
    - Return and risk metrics
    - Trade statistics
    - Time-based performance breakdown
    """

    def __init__(
        self,
        equity_curve: pd.Series,
        trades: List[TradeRecord],
        initial_capital: float,
        risk_free_rate: float = 0.0,
        periods_per_year: Optional[int] = None,
    ):
        """
        Initialize performance analyzer.

        Args:
            equity_curve: Equity curve with DatetimeIndex
            trades: List of trade records
            initial_capital: Starting capital
            risk_free_rate: Annual risk-free rate (default 0)
            periods_per_year: Bars per year for annualisation.  When *None*
                (default) the value is inferred automatically from the
                equity curve's DatetimeIndex, so 1-hour or daily strategies
                are annualised correctly without any extra configuration.
        """
        self.equity_curve = equity_curve
        self.trades = trades
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate
        if periods_per_year is None:
            self.periods_per_year = infer_periods_per_year(equity_curve.index)
        else:
            self.periods_per_year = periods_per_year

        # Calculate returns
        self.returns = equity_curve.pct_change().dropna()

    def calculate_metrics(self) -> Dict[str, float]:
        """
        Calculate all performance metrics.

        Returns:
            Dictionary with all metrics
        """
        metrics = {}

        # Basic return metrics
        metrics.update(self._calculate_return_metrics())

        # Risk metrics
        metrics.update(self._calculate_risk_metrics())

        # Trade statistics
        metrics.update(self._calculate_trade_stats())

        # TradingView-compatible metrics
        metrics.update(self._calculate_tv_metrics())

        return metrics

    def _calculate_return_metrics(self) -> Dict[str, float]:
        """Calculate return-related metrics."""
        final_equity = self.equity_curve.iloc[-1]
        total_return = (final_equity - self.initial_capital) / self.initial_capital
        total_return_pct = total_return * 100

        # Annualized return
        n_periods = len(self.equity_curve)
        years = n_periods / self.periods_per_year
        if years > 0 and total_return > -1:
            annualized_return = (1 + total_return) ** (1 / years) - 1
            annualized_return_pct = annualized_return * 100
        else:
            annualized_return_pct = 0.0

        return {
            "total_return_pct": total_return_pct,
            "annualized_return_pct": annualized_return_pct,
            "final_equity": final_equity,
        }

    def _calculate_risk_metrics(self) -> Dict[str, float]:
        """Calculate risk-related metrics."""
        # Maximum drawdown
        rolling_max = self.equity_curve.cummax()
        drawdown = (self.equity_curve - rolling_max) / rolling_max
        max_drawdown_pct = abs(drawdown.min()) * 100 if len(drawdown) > 0 else 0.0

        # Sharpe ratio
        if len(self.returns) > 1 and self.returns.std() > 0:
            excess_returns = self.returns - self.risk_free_rate / self.periods_per_year
            sharpe = excess_returns.mean() / self.returns.std() * np.sqrt(self.periods_per_year)
        else:
            sharpe = 0.0

        # Sortino ratio (only penalizes downside volatility)
        downside_returns = self.returns[self.returns < 0]
        if len(downside_returns) > 1 and downside_returns.std() > 0:
            excess_returns = self.returns.mean() - self.risk_free_rate / self.periods_per_year
            sortino = excess_returns / downside_returns.std() * np.sqrt(self.periods_per_year)
        else:
            sortino = 0.0

        # Calmar ratio (annualized return / max drawdown)
        return_metrics = self._calculate_return_metrics()
        annualized_return_pct = return_metrics["annualized_return_pct"]
        if max_drawdown_pct > 0:
            calmar = annualized_return_pct / max_drawdown_pct
        else:
            calmar = 0.0

        # Annualized volatility
        if len(self.returns) > 1:
            ann_vol = float(self.returns.std() * np.sqrt(self.periods_per_year) * 100)
        else:
            ann_vol = 0.0

        # Max drawdown duration (in calendar days)
        rolling_max = self.equity_curve.cummax()
        drawdown = (self.equity_curve - rolling_max) / rolling_max
        max_dd_duration_days = 0.0
        if len(drawdown) > 1:
            in_dd = False
            dd_start = drawdown.index[0]
            for ts, dd_val in drawdown.items():
                if dd_val < 0 and not in_dd:
                    in_dd = True
                    dd_start = ts
                elif dd_val >= 0 and in_dd:
                    in_dd = False
                    dur = (ts - dd_start).total_seconds() / 86400
                    if dur > max_dd_duration_days:
                        max_dd_duration_days = dur
            if in_dd:
                dur = (drawdown.index[-1] - dd_start).total_seconds() / 86400
                if dur > max_dd_duration_days:
                    max_dd_duration_days = dur

        # Recovery factor (total return / max drawdown)
        total_return_pct = return_metrics["total_return_pct"]
        recovery_factor = total_return_pct / max_drawdown_pct if max_drawdown_pct > 0 else 0.0

        return {
            "max_drawdown_pct": max_drawdown_pct,
            "max_dd_duration_days": max_dd_duration_days,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "annualized_volatility_pct": ann_vol,
            "recovery_factor": recovery_factor,
        }

    def _calculate_trade_stats(self) -> Dict[str, float]:
        """Calculate trade statistics."""
        # Filter closing trades (those with PnL)
        closing_trades = [t for t in self.trades if t.pnl != 0]
        total_trades = len(closing_trades)

        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "payoff_ratio": 0.0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
                "avg_trade_duration_hours": 0.0,
            }

        # Winning and losing trades
        winning_trades = [t for t in closing_trades if t.pnl > 0]
        losing_trades = [t for t in closing_trades if t.pnl < 0]

        n_wins = len(winning_trades)
        n_losses = len(losing_trades)

        # Win rate
        win_rate = n_wins / total_trades * 100

        # Gross profit and loss
        gross_profit = sum(t.pnl for t in winning_trades)
        gross_loss = abs(sum(t.pnl for t in losing_trades))

        # Profit factor
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            # All trades are winners or no trades
            profit_factor = gross_profit if gross_profit > 0 else 0.0

        # Average win/loss
        avg_win = gross_profit / n_wins if n_wins > 0 else 0.0
        avg_loss = gross_loss / n_losses if n_losses > 0 else 0.0

        # Payoff ratio (avg win / avg loss)
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        # Expectancy
        loss_rate = n_losses / total_trades if total_trades > 0 else 0
        expectancy = (win_rate / 100 * avg_win) - (loss_rate * avg_loss)

        # Largest win/loss
        largest_win = max(t.pnl for t in winning_trades) if winning_trades else 0.0
        largest_loss = abs(min(t.pnl for t in losing_trades)) if losing_trades else 0.0

        # Max consecutive wins/losses
        max_con_wins = 0
        max_con_losses = 0
        cur_wins = 0
        cur_losses = 0
        for t in closing_trades:
            if t.pnl > 0:
                cur_wins += 1
                cur_losses = 0
                max_con_wins = max(max_con_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_con_losses = max(max_con_losses, cur_losses)

        # Average trade duration (hours)
        # TradeRecord has timestamp (exit) but not entry; use bar index diff
        # For now, compute from equity curve bar intervals × bars held
        avg_trade_duration_hours = 0.0
        if len(self.equity_curve.index) >= 2:
            median_bar_secs = (
                pd.Series(self.equity_curve.index.asi8).diff().dropna().median() / 1e9
            )
            # Approximate: each trade's bar count = (exit_idx - entry_idx)
            # TradeRecord doesn't store bar indices, so estimate from total
            # bars / total trades as average holding period
            total_bars = len(self.equity_curve)
            avg_bars_per_trade = total_bars / total_trades if total_trades > 0 else 0
            avg_trade_duration_hours = avg_bars_per_trade * median_bar_secs / 3600

        return {
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
            "payoff_ratio": payoff_ratio,
            "max_consecutive_wins": max_con_wins,
            "max_consecutive_losses": max_con_losses,
            "avg_trade_duration_hours": avg_trade_duration_hours,
        }

    def _calculate_tv_metrics(self) -> Dict[str, float]:
        """Calculate TradingView Strategy Tester-compatible metrics."""
        closing_trades = [t for t in self.trades if t.pnl != 0]
        total_trades = len(closing_trades)

        # Commission paid (sum of all trade fees)
        commission_paid = sum(t.fee for t in self.trades)

        # Net profit ($)
        final_equity = self.equity_curve.iloc[-1]
        net_profit = final_equity - self.initial_capital

        # Avg trade ($ and %)
        if total_trades > 0:
            avg_trade_dollar = net_profit / total_trades
            avg_trade_pct = sum(t.pnl_pct for t in closing_trades) / total_trades
        else:
            avg_trade_dollar = 0.0
            avg_trade_pct = 0.0

        # Avg bars held (overall, winning, losing) — requires bars_held field
        winning = [t for t in closing_trades if t.pnl > 0]
        losing = [t for t in closing_trades if t.pnl < 0]

        bars_all = [t.bars_held for t in closing_trades if t.bars_held > 0]
        bars_winning = [t.bars_held for t in winning if t.bars_held > 0]
        bars_losing = [t.bars_held for t in losing if t.bars_held > 0]

        avg_bars_held = sum(bars_all) / len(bars_all) if bars_all else 0.0
        avg_bars_held_winning = sum(bars_winning) / len(bars_winning) if bars_winning else 0.0
        avg_bars_held_losing = sum(bars_losing) / len(bars_losing) if bars_losing else 0.0

        # Open PL (unrealized) — non-zero only when last trade leaves an open position
        open_pl = 0.0
        if self.trades:
            last_trade = self.trades[-1]
            if last_trade.position_after != 0:
                # Find the capital after the most recent flat (closed) state
                last_closed_capital = self.initial_capital
                for t in reversed(self.trades):
                    if t.position_after == 0:
                        last_closed_capital = t.capital_after
                        break
                open_pl = final_equity - last_closed_capital

        return {
            "net_profit": net_profit,
            "commission_paid": commission_paid,
            "avg_trade_dollar": avg_trade_dollar,
            "avg_trade_pct": avg_trade_pct,
            "avg_bars_held": avg_bars_held,
            "avg_bars_held_winning": avg_bars_held_winning,
            "avg_bars_held_losing": avg_bars_held_losing,
            "open_pl": open_pl,
        }

    def trade_sharpe_ratio(self) -> float:
        """Compute trade-based Sharpe ratio (per-trade returns, annualised by trade frequency).

        TradingView's Strategy Tester uses individual trade returns rather than bar-level
        equity curve returns.  This method mirrors that approach.
        """
        closing_trades = [t for t in self.trades if t.pnl != 0]
        if len(closing_trades) < 2:
            return 0.0
        returns = np.array([t.pnl_pct / 100.0 for t in closing_trades])
        std = returns.std()
        if std < 1e-12:
            return 0.0
        # Annualise: estimate trades per year from equity curve time span
        if len(self.equity_curve) >= 2:
            span_secs = (
                self.equity_curve.index[-1] - self.equity_curve.index[0]
            ).total_seconds()
            span_years = span_secs / (365.25 * 86400)
            trades_per_year = len(closing_trades) / span_years if span_years > 0 else len(closing_trades)
        else:
            trades_per_year = len(closing_trades)
        return float(returns.mean() / std * np.sqrt(trades_per_year))

    def tv_compatible_report(self, bh_return_pct: Optional[float] = None) -> str:
        """Return a TradingView Strategy Tester-compatible formatted report string.

        Args:
            bh_return_pct: Optional buy-and-hold return % for the same period.
                           Pass this from the runner where price data is available.
        """
        metrics = self.calculate_metrics()
        tv = self._calculate_tv_metrics()
        trade_sharpe = self.trade_sharpe_ratio()
        bh = bh_return_pct if bh_return_pct is not None else 0.0

        lines = [
            "=" * 62,
            "TRADINGVIEW STRATEGY TESTER — COMPATIBLE REPORT",
            "=" * 62,
            "",
            "OVERVIEW",
            f"  Net Profit:             ${tv['net_profit']:>+12,.2f}   ({metrics['total_return_pct']:+.2f}%)",
            f"  Gross Profit:           ${metrics['gross_profit']:>12,.2f}",
            f"  Gross Loss:             ${metrics['gross_loss']:>12,.2f}",
            f"  Buy & Hold Return:      {'':>13}{bh:+.2f}%",
            f"  Max Drawdown:           {'':>13}{metrics['max_drawdown_pct']:.2f}%",
            f"  Commission Paid:        ${tv['commission_paid']:>12,.2f}",
            f"  Open PL:                ${tv['open_pl']:>+12,.2f}",
            "",
            "RATIOS",
            f"  Sharpe (equity curve):  {'':>13}{metrics['sharpe_ratio']:.2f}",
            f"  Sharpe (trade-based):   {'':>13}{trade_sharpe:.2f}",
            f"  Sortino:                {'':>13}{metrics['sortino_ratio']:.2f}",
            f"  Calmar:                 {'':>13}{metrics['calmar_ratio']:.2f}",
            f"  Profit Factor:          {'':>13}{metrics['profit_factor']:.2f}",
            f"  Payoff Ratio:           {'':>13}{metrics['payoff_ratio']:.2f}",
            "",
            "TRADE STATISTICS",
            f"  Total Trades:           {'':>13}{metrics['total_trades']}",
            f"  Win Rate:               {'':>13}{metrics['win_rate_pct']:.1f}%",
            f"  Avg Trade:              ${tv['avg_trade_dollar']:>+12,.2f}   ({tv['avg_trade_pct']:+.2f}%)",
            f"  Avg Winning Trade:      ${metrics['avg_win']:>12,.2f}",
            f"  Avg Losing Trade:       ${metrics['avg_loss']:>12,.2f}",
            f"  Largest Win:            ${metrics['largest_win']:>12,.2f}",
            f"  Largest Loss:           ${metrics['largest_loss']:>12,.2f}",
            f"  Max Consec. Wins:       {'':>13}{metrics['max_consecutive_wins']}",
            f"  Max Consec. Losses:     {'':>13}{metrics['max_consecutive_losses']}",
        ]
        if tv["avg_bars_held"] > 0:
            lines += [
                f"  Avg Bars Held:          {'':>13}{tv['avg_bars_held']:.1f}",
                f"  Avg Bars (winning):     {'':>13}{tv['avg_bars_held_winning']:.1f}",
                f"  Avg Bars (losing):      {'':>13}{tv['avg_bars_held_losing']:.1f}",
            ]
        lines += ["=" * 62]
        return "\n".join(lines)

    def get_daily_returns(self) -> pd.Series:
        """
        Calculate daily returns.

        Returns:
            Series of daily returns
        """
        # Resample equity curve to daily
        daily_equity = self.equity_curve.resample("D").last()
        daily_returns = daily_equity.pct_change().dropna()
        return daily_returns

    def get_monthly_returns(self) -> pd.Series:
        """
        Calculate monthly returns.

        Returns:
            Series of monthly returns
        """
        # Resample equity curve to monthly
        monthly_equity = self.equity_curve.resample("ME").last()
        monthly_returns = monthly_equity.pct_change().dropna()
        return monthly_returns

    def get_drawdown_series(self) -> pd.Series:
        """
        Get drawdown time series.

        Returns:
            Series of drawdown values
        """
        rolling_max = self.equity_curve.cummax()
        drawdown = (self.equity_curve - rolling_max) / rolling_max
        return drawdown

    def get_underwater_periods(self) -> List[Dict]:
        """
        Get periods of drawdown (underwater periods).

        Returns:
            List of drawdown periods with start, end, and depth
        """
        drawdown = self.get_drawdown_series()
        periods = []

        in_drawdown = False
        start = None
        max_dd = 0.0

        for i, (timestamp, dd) in enumerate(drawdown.items()):
            if dd < 0 and not in_drawdown:
                # Start of drawdown
                in_drawdown = True
                start = timestamp
                max_dd = dd
            elif dd < 0 and in_drawdown:
                # Continue drawdown
                max_dd = min(max_dd, dd)
            elif dd >= 0 and in_drawdown:
                # End of drawdown
                in_drawdown = False
                periods.append({
                    "start": start,
                    "end": timestamp,
                    "max_drawdown_pct": abs(max_dd) * 100,
                })

        # Handle ongoing drawdown
        if in_drawdown:
            periods.append({
                "start": start,
                "end": drawdown.index[-1],
                "max_drawdown_pct": abs(max_dd) * 100,
            })

        return periods

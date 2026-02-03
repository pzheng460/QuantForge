"""
Performance analysis for backtest results.

Calculates comprehensive trading performance metrics including:
- Return metrics (total, annualized)
- Risk metrics (Sharpe, Sortino, Calmar, max drawdown)
- Trade statistics (win rate, profit factor, expectancy)
- Time-based analysis (daily, monthly returns)
"""

from typing import Dict, List

import numpy as np
import pandas as pd

from nexustrader.backtest.result import TradeRecord


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
        periods_per_year: int = 4 * 24 * 365,  # 15-min bars
    ):
        """
        Initialize performance analyzer.

        Args:
            equity_curve: Equity curve with DatetimeIndex
            trades: List of trade records
            initial_capital: Starting capital
            risk_free_rate: Annual risk-free rate (default 0)
            periods_per_year: Number of trading periods per year
        """
        self.equity_curve = equity_curve
        self.trades = trades
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate
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

        return {
            "max_drawdown_pct": max_drawdown_pct,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
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

        # Expectancy
        loss_rate = n_losses / total_trades if total_trades > 0 else 0
        expectancy = (win_rate / 100 * avg_win) - (loss_rate * avg_loss)

        # Largest win/loss
        largest_win = max(t.pnl for t in winning_trades) if winning_trades else 0.0
        largest_loss = abs(min(t.pnl for t in losing_trades)) if losing_trades else 0.0

        return {
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "largest_win": largest_win,
            "largest_loss": largest_loss,
        }

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

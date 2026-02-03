"""
Market regime classification for backtest analysis.

Classifies market conditions into regimes:
- Trending up/down
- Ranging/sideways
- High volatility

Enables performance analysis by market regime.
"""

from enum import Enum
from typing import Dict

import pandas as pd


class MarketRegime(Enum):
    """Market regime types."""

    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"


class RegimeClassifier:
    """
    Classify market regimes based on price data.

    Uses moving averages for trend detection and ATR for volatility.
    """

    def __init__(
        self,
        trend_lookback: int = 20,
        trend_threshold: float = 0.01,
        volatility_lookback: int = 14,
        volatility_threshold: float = 2.0,
    ):
        """
        Initialize regime classifier.

        Args:
            trend_lookback: Lookback period for trend calculation
            trend_threshold: Minimum return for trend classification
            volatility_lookback: Lookback period for volatility calculation
            volatility_threshold: Multiplier for high volatility threshold
        """
        self.trend_lookback = trend_lookback
        self.trend_threshold = trend_threshold
        self.volatility_lookback = volatility_lookback
        self.volatility_threshold = volatility_threshold

    def classify(self, data: pd.DataFrame) -> pd.Series:
        """
        Classify market regime for each bar.

        Args:
            data: OHLCV DataFrame with DatetimeIndex

        Returns:
            Series of regime labels
        """
        n = len(data)
        regimes = pd.Series(index=data.index, dtype=str)

        # Calculate indicators
        close = data["close"]

        # Trend: Rate of change over lookback period
        if n >= self.trend_lookback:
            roc = close.pct_change(self.trend_lookback)
        else:
            roc = close.pct_change(max(1, n - 1))

        # Volatility: Rolling standard deviation of returns
        returns = close.pct_change()
        if n >= self.volatility_lookback:
            rolling_vol = returns.rolling(self.volatility_lookback).std()
            avg_vol = returns.std()
        else:
            rolling_vol = returns.rolling(max(2, n)).std()
            avg_vol = returns.std() if len(returns.dropna()) > 0 else 0.001

        # Handle edge case of zero volatility
        if avg_vol == 0 or pd.isna(avg_vol):
            avg_vol = 0.001

        # Classify each bar
        for i in range(n):
            current_roc = roc.iloc[i] if not pd.isna(roc.iloc[i]) else 0.0
            current_vol = rolling_vol.iloc[i] if not pd.isna(rolling_vol.iloc[i]) else 0.0

            # Check for high volatility first
            if current_vol > avg_vol * self.volatility_threshold:
                regimes.iloc[i] = MarketRegime.HIGH_VOLATILITY.value
            # Check for trend
            elif current_roc > self.trend_threshold:
                regimes.iloc[i] = MarketRegime.TRENDING_UP.value
            elif current_roc < -self.trend_threshold:
                regimes.iloc[i] = MarketRegime.TRENDING_DOWN.value
            # Otherwise ranging
            else:
                regimes.iloc[i] = MarketRegime.RANGING.value

        return regimes

    def get_performance_by_regime(
        self,
        regimes: pd.Series,
        equity_curve: pd.Series,
    ) -> Dict[str, Dict[str, float]]:
        """
        Calculate performance metrics for each regime.

        Args:
            regimes: Series of regime labels (from classify())
            equity_curve: Equity curve Series

        Returns:
            Dictionary mapping regime to performance metrics
        """
        performance = {}

        # Calculate returns
        returns = equity_curve.pct_change()

        # Group by regime
        unique_regimes = regimes.unique()

        for regime in unique_regimes:
            mask = regimes == regime
            regime_returns = returns[mask].dropna()

            if len(regime_returns) > 0:
                # Calculate metrics
                total_return = (1 + regime_returns).prod() - 1
                avg_return = regime_returns.mean()
                volatility = regime_returns.std() if len(regime_returns) > 1 else 0.0

                performance[regime] = {
                    "return_pct": total_return * 100,
                    "avg_return_pct": avg_return * 100,
                    "volatility_pct": volatility * 100,
                    "count": len(regime_returns),
                }
            else:
                performance[regime] = {
                    "return_pct": 0.0,
                    "avg_return_pct": 0.0,
                    "volatility_pct": 0.0,
                    "count": 0,
                }

        return performance

    def get_regime_summary(self, regimes: pd.Series) -> Dict[str, float]:
        """
        Get summary statistics of regime distribution.

        Args:
            regimes: Series of regime labels

        Returns:
            Dictionary with regime percentages
        """
        total = len(regimes)
        if total == 0:
            return {}

        counts = regimes.value_counts()
        summary = {}

        for regime in MarketRegime:
            count = counts.get(regime.value, 0)
            summary[f"{regime.value}_pct"] = count / total * 100

        return summary

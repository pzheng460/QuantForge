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


class SimpleRegime(Enum):
    """Simple 3-state regime classification (US-10 spec)."""

    BULL = "bull"  # 上涨 > 20%
    BEAR = "bear"  # 下跌 > 20%
    RANGING = "ranging"  # 其他


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

    def classify_simple(
        self,
        data: pd.DataFrame,
        bull_threshold: float = 0.20,
        bear_threshold: float = 0.20,
        lookback: int = None,
    ) -> pd.Series:
        """
        Simple 3-state regime classification (US-10 spec) - Production version.

        Uses expanding window from recent pivot points to classify market state,
        detecting when price has moved significantly from local highs or lows.

        Classification logic:
        - Bull: Price has risen > bull_threshold (20%) from recent low
        - Bear: Price has fallen > bear_threshold (20%) from recent high
        - Ranging: otherwise

        This approach:
        1. Tracks expanding high/low from data start (cumulative extremes)
        2. Uses multiple rolling windows to detect regimes at different scales
        3. Robust to both fast and gradual market moves

        Args:
            data: OHLCV DataFrame with DatetimeIndex
            bull_threshold: Threshold for bull market (default 0.20 = 20%)
            bear_threshold: Threshold for bear market (default 0.20 = 20%)
            lookback: Rolling window for return calculation. If None, auto-calculated

        Returns:
            Series of SimpleRegime labels
        """
        n = len(data)
        regimes = pd.Series(index=data.index, dtype=str)
        close = data["close"]

        # Auto-calculate lookback if not provided
        if lookback is None:
            if n >= 2:
                time_diff = (data.index[-1] - data.index[0]).total_seconds()
                if time_diff > 0:
                    total_days = time_diff / 86400
                    bars_per_day = n / total_days
                    if total_days >= 30:
                        lookback = int(bars_per_day * 30)
                    else:
                        lookback = max(10, int(n * 0.3))
                else:
                    lookback = max(10, n // 3)
                lookback = max(10, min(lookback, int(n * 0.8)))
            else:
                lookback = max(1, n - 1)

        # Use EXPANDING window for cumulative high/low from start
        # This tracks the historical high and low seen so far
        expanding_max = close.expanding(min_periods=1).max()
        expanding_min = close.expanding(min_periods=1).min()

        # Also calculate rolling returns at multiple periods
        lookback_periods = [
            lookback,
            max(5, lookback // 2),
            max(3, lookback // 4),
        ]
        rolling_returns = {lb: close.pct_change(lb) for lb in lookback_periods}

        # Classify each bar
        for i in range(n):
            current_price = close.iloc[i]
            cum_high = expanding_max.iloc[i]
            cum_low = expanding_min.iloc[i]

            # Calculate distance from cumulative high/low
            # This measures how far price has moved from historical extremes
            if cum_low > 0:
                dist_from_low = (current_price - cum_low) / cum_low
            else:
                dist_from_low = 0.0

            if cum_high > 0:
                dist_from_high = (current_price - cum_high) / cum_high
            else:
                dist_from_high = 0.0

            # Also check rolling returns for additional confirmation
            max_rolling_ret = 0.0
            min_rolling_ret = 0.0
            for lb in lookback_periods:
                if i >= lb:
                    ret = rolling_returns[lb].iloc[i]
                    if not pd.isna(ret):
                        max_rolling_ret = max(max_rolling_ret, ret)
                        min_rolling_ret = min(min_rolling_ret, ret)

            # Classification:
            # Bull: Price is significantly above the historical low
            # Bear: Price is significantly below the historical high
            is_bull = dist_from_low > bull_threshold or max_rolling_ret > bull_threshold
            is_bear = dist_from_high < -bear_threshold or min_rolling_ret < -bear_threshold

            if is_bull and not is_bear:
                regimes.iloc[i] = SimpleRegime.BULL.value
            elif is_bear and not is_bull:
                regimes.iloc[i] = SimpleRegime.BEAR.value
            elif is_bull and is_bear:
                # Both conditions - decide by which is stronger
                bull_strength = max(dist_from_low, max_rolling_ret)
                bear_strength = max(abs(dist_from_high), abs(min_rolling_ret))
                if bull_strength > bear_strength:
                    regimes.iloc[i] = SimpleRegime.BULL.value
                else:
                    regimes.iloc[i] = SimpleRegime.BEAR.value
            else:
                regimes.iloc[i] = SimpleRegime.RANGING.value

        return regimes

    def get_simple_regime_summary(self, regimes: pd.Series) -> Dict[str, float]:
        """
        Get summary statistics for simple regime distribution.

        Args:
            regimes: Series of SimpleRegime labels

        Returns:
            Dictionary with regime percentages
        """
        total = len(regimes)
        if total == 0:
            return {}

        counts = regimes.value_counts()
        summary = {}

        for regime in SimpleRegime:
            count = counts.get(regime.value, 0)
            summary[f"{regime.value}_pct"] = count / total * 100

        return summary

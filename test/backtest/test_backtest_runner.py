"""Tests for BacktestRunner with mock data for all 3 strategies."""

import numpy as np
import pandas as pd
import pytest

from strategy.backtest.runner import BacktestRunner


def _make_mock_data(n_bars: int = 500) -> pd.DataFrame:
    """Generate synthetic OHLCV data for testing."""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n_bars, freq="15min")
    close = 50000 + np.cumsum(np.random.randn(n_bars) * 100)
    high = close + np.abs(np.random.randn(n_bars) * 50)
    low = close - np.abs(np.random.randn(n_bars) * 50)
    open_ = close + np.random.randn(n_bars) * 30
    volume = np.random.rand(n_bars) * 1000

    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class TestBacktestRunnerInit:
    """Test BacktestRunner initialization."""

    def test_init_hurst_kalman(self):
        """BacktestRunner should initialize for hurst_kalman."""
        import strategy.strategies  # noqa: F401

        runner = BacktestRunner(strategy_name="hurst_kalman", exchange="bitget")
        assert runner.reg.name == "hurst_kalman"
        assert runner.profile.ccxt_id == "bitget"
        assert runner.symbol == "BTC/USDT:USDT"

    def test_init_ema_crossover(self):
        """BacktestRunner should initialize for ema_crossover."""
        import strategy.strategies  # noqa: F401

        runner = BacktestRunner(strategy_name="ema_crossover", exchange="binance")
        assert runner.reg.name == "ema_crossover"
        assert runner.profile.ccxt_id == "binance"

    def test_init_bollinger_band(self):
        """BacktestRunner should initialize for bollinger_band."""
        import strategy.strategies  # noqa: F401

        runner = BacktestRunner(strategy_name="bollinger_band", exchange="okx")
        assert runner.reg.name == "bollinger_band"
        assert runner.profile.ccxt_id == "okx"

    def test_init_custom_symbol(self):
        """Custom symbol should override the exchange default."""
        import strategy.strategies  # noqa: F401

        runner = BacktestRunner(
            strategy_name="hurst_kalman",
            exchange="bitget",
            symbol="ETH/USDT:USDT",
        )
        assert runner.symbol == "ETH/USDT:USDT"

    def test_init_unknown_strategy_raises(self):
        """Unknown strategy name should raise KeyError."""
        with pytest.raises(KeyError):
            BacktestRunner(strategy_name="nonexistent", exchange="bitget")

    def test_init_unknown_exchange_raises(self):
        """Unknown exchange name should raise KeyError."""
        import strategy.strategies  # noqa: F401

        with pytest.raises(KeyError):
            BacktestRunner(strategy_name="hurst_kalman", exchange="nonexistent")


class TestBacktestRunnerRunSingle:
    """Test the run_single method with mock data using config_override."""

    def test_run_single_hurst_kalman(self):
        """run_single should return a dict with expected keys for HK."""
        import strategy.strategies  # noqa: F401

        from strategy.strategies.hurst_kalman.core import HurstKalmanConfig
        from strategy.strategies.hurst_kalman.signal import TradeFilterConfig

        runner = BacktestRunner(strategy_name="hurst_kalman", exchange="bitget")
        data = _make_mock_data(1000)

        result = runner.run_single(
            data,
            config_override=HurstKalmanConfig(),
            filter_override=TradeFilterConfig(),
        )
        assert isinstance(result, dict)
        assert "total_return_pct" in result

    def test_run_single_ema(self):
        """run_single should work for EMA crossover."""
        import strategy.strategies  # noqa: F401

        from strategy.strategies.ema_crossover.core import EMAConfig
        from strategy.strategies.ema_crossover.signal import EMATradeFilterConfig

        runner = BacktestRunner(strategy_name="ema_crossover", exchange="bitget")
        data = _make_mock_data(1000)

        result = runner.run_single(
            data,
            config_override=EMAConfig(),
            filter_override=EMATradeFilterConfig(),
        )
        assert isinstance(result, dict)
        assert "total_return_pct" in result

    def test_run_single_bb(self):
        """run_single should work for Bollinger Band."""
        import strategy.strategies  # noqa: F401

        from strategy.strategies.bollinger_band.core import BBConfig
        from strategy.strategies.bollinger_band.signal import BBTradeFilterConfig

        runner = BacktestRunner(strategy_name="bollinger_band", exchange="bitget")
        data = _make_mock_data(1000)

        result = runner.run_single(
            data,
            config_override=BBConfig(),
            filter_override=BBTradeFilterConfig(),
        )
        assert isinstance(result, dict)
        assert "total_return_pct" in result

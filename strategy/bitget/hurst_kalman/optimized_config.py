from strategy.bitget.hurst_kalman.configs import StrategyConfig, TradeFilterConfig
from strategy.bitget.hurst_kalman.core import HurstKalmanConfig

# =============================================================================
# OPTIMIZED CONFIG (Generated: 2026-02-03 12:13)
# Period: 6m
# Performance: 11.3% return, 3.58 Sharpe
# =============================================================================

OPTIMIZED_CONFIG = StrategyConfig(
    name="Optimized",
    description="Auto-optimized parameters from grid search",
    risk_level="medium",
    recommended=False,
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window=100,
        zscore_window=60,
        zscore_entry=3.5,
        mean_reversion_threshold=0.4,
        trend_threshold=0.60,
        kalman_R=0.3,
        kalman_Q=5e-05,
        position_size_pct=0.10,
        stop_loss_pct=0.03,
        daily_loss_limit=0.03,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=8,
        cooldown_bars=4,
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
)

# To use in paper trading, add to configs.py and set:
# SELECTED_CONFIG = OPTIMIZED_CONFIG

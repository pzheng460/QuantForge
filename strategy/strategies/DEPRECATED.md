# Deprecated Strategies

These strategies were tested and found to be unprofitable (negative Sharpe ratios).
They are kept for reference but should NOT be used in production.

| Strategy | Reason | Notes |
|----------|--------|-------|
| `vwap/` | Negative Sharpe, Z-Score issues | -5.41% in simulation |
| `grid_trading/` | All parameter combos lost money | High leverage made it worse |
| `dynamic_grid/` | Variant of grid_trading, also failed | Volatility-adaptive leverage didn't help |
| `fear_reversal/` | Never used in production | Long-only fear bounce reversal |
| `funding_arb/` | Not actively used | Delta-neutral funding rate arbitrage |
| `funding_rate/` | Not actively used | Funding rate strategy |
| `sma_funding/` | Not actively used | Dual-leg: SMA trend + funding arb |
| `ma_convergence/` | Not actively used | Moving average convergence breakout |
| `regime_ema/` | Not actively used | EMA crossover + regime filter |

## Active Strategies

| Strategy | Description |
|----------|-------------|
| `_base/` | Framework (always needed) |
| `ema_crossover/` | Simple EMA crossover, validated against TradingView |
| `momentum/` | Momentum + ADX filter, Sharpe 2.8 (best performer) |
| `dual_regime/` | ADX regime switching, promising holdout results |
| `bollinger_band/` | Used by dual_regime |
| `hurst_kalman/` | Statistical arb, interesting but low frequency |
| `sma_trend/` | Simple SMA trend following |

## Known Duplication (do not remove yet — may break backtest)

`calculate_ema()` and `calculate_ema_single()` are duplicated across:
- `ema_crossover/core.py` (vectorized numpy, for backtest)
- `momentum/core.py` (same implementation)
- `regime_ema/core.py` (same implementation, deprecated)

These are functionally identical to `StreamingEMA` in `_base/streaming.py` but operate on numpy arrays (vectorized) vs single values (streaming). The backtest signal generators depend on the vectorized versions.

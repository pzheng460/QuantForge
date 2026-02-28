# Plan: Unify All Strategy Signal Logic

## Goal
Apply the same refactoring pattern used for momentum to all 8 remaining strategies: extract shared streaming signal cores into `strategy/indicators/`, then refactor both backtest signal generators and live trading indicators to delegate to them.

## Reference Pattern (Momentum - already done)
```
strategy/indicators/momentum.py          → MomentumSignalCore (shared core)
strategy/strategies/momentum/signal.py   → delegates to MomentumSignalCore
strategy/bitget/momentum/indicator.py    → delegates to MomentumSignalCore
test/indicators/test_momentum_parity.py  → parity test
```

## Strategies to Unify

### 1. hurst_kalman
- **Core**: `strategy/indicators/hurst_kalman.py` → `HurstKalmanSignalCore`
  - Streaming: KalmanFilter1D, calculate_hurst, zscore calculation
  - Position management: stop loss, market state classification, cooldown, signal confirmation
- **Backtest**: Refactor `strategy/strategies/hurst_kalman/signal.py` to use core
- **Live**: Refactor `strategy/bitget/hurst_kalman/indicator.py` to use core
- **Test**: `test/indicators/test_hurst_kalman_parity.py`

### 2. ema_crossover
- **Core**: `strategy/indicators/ema_crossover.py` → `EMASignalCore`
  - Streaming: 2x StreamingEMA (fast/slow), crossover detection (prev_diff/curr_diff)
  - Position management: stop loss, cooldown, signal confirmation
- **Backtest**: Refactor `strategy/strategies/ema_crossover/signal.py` to use core
- **Live**: Refactor `strategy/bitget/ema_crossover/indicator.py` to use core
- **Test**: `test/indicators/test_ema_crossover_parity.py`

### 3. bollinger_band
- **Core**: `strategy/indicators/bollinger_band.py` → `BBSignalCore`
  - Streaming: StreamingSMA-based BB calculation (deque window), trend SMA
  - Signal: mean reversion (price vs bands), trend bias filter, exit threshold
  - Position management: stop loss, cooldown, signal confirmation
- **Backtest**: Refactor `strategy/strategies/bollinger_band/signal.py` to use core
- **Live**: Refactor `strategy/bitget/bollinger_band/indicator.py` to use core
- **Test**: `test/indicators/test_bollinger_band_parity.py`

### 4. vwap
- **Core**: `strategy/indicators/vwap.py` → `VWAPSignalCore`
  - Streaming: Cumulative VWAP (with daily reset), rolling zscore, Wilder's RSI
  - Signal: zscore + RSI confirmation for entries, zscore exit, model failure stop
  - Position management: stop loss, cooldown, signal confirmation
- **Backtest**: Refactor `strategy/strategies/vwap/signal.py` to use core
- **Live**: Refactor `strategy/bitget/vwap/indicator.py` to use core
- **Test**: `test/indicators/test_vwap_parity.py`

### 5. regime_ema
- **Core**: `strategy/indicators/regime_ema.py` → `RegimeEMASignalCore`
  - Streaming: 2x StreamingEMA, StreamingATR, StreamingADX, rolling ATR mean (deque)
  - Signal: EMA crossover gated on regime (trending vs ranging), auto-close on ranging
  - Position management: stop loss, cooldown, signal confirmation
- **Backtest**: Refactor `strategy/strategies/regime_ema/signal.py` to use core
- **Live**: Refactor `strategy/bitget/regime_ema/indicator.py` to use core
- **Test**: `test/indicators/test_regime_ema_parity.py`

### 6. funding_rate
- **Core**: `strategy/indicators/funding_rate.py` → `FundingRateSignalCore`
  - Streaming: StreamingSMA for price, funding rate tracking (deque for avg)
  - Signal: timing-based (hours to/from settlement), funding rate threshold, trend filter
  - Position management: stop loss, adverse move check, cooldown
  - Note: `update()` takes (close, hours_to_next, hours_since_last) plus set_funding_rate()
- **Backtest**: Refactor `strategy/strategies/funding_rate/signal.py` to use core
- **Live**: Refactor `strategy/bitget/funding_rate/indicator.py` to use core
- **Test**: `test/indicators/test_funding_rate_parity.py`

### 7. dual_regime (backtest only - no live indicator)
- **Core**: `strategy/indicators/dual_regime.py` → `DualRegimeSignalCore`
  - Streaming: Combines momentum indicators (ROC, EMA, ATR, ADX, vol SMA) + BB
  - Signal: ADX-based regime switching between momentum and BB strategies
  - Position management: regime switch close, trailing stop, cooldown
- **Backtest**: Refactor `strategy/strategies/dual_regime/signal.py` to use core
- **No live indicator** to refactor
- **Test**: `test/indicators/test_dual_regime_parity.py`

### 8. grid_trading (complex - different computation model)
- **Core**: `strategy/indicators/grid_trading.py` → `GridSignalCore`
  - Streaming: SMA + ATR for grid bounds, grid level tracking
  - Signal: grid line crossings for entry/exit, peak/trough tracking
  - Position management: stop loss, cooldown, grid recalculation
- **Backtest**: Refactor `strategy/strategies/grid_trading/signal.py` to use core
- **Live**: Refactor `strategy/bitget/grid/indicator.py` to use core
- **Test**: `test/indicators/test_grid_trading_parity.py`

## Implementation Order
1. Start with simpler strategies (ema_crossover, bollinger_band)
2. Then medium complexity (hurst_kalman, vwap, regime_ema)
3. Then complex (funding_rate, dual_regime, grid_trading)
4. Run all tests and lint checks after each batch

## New Streaming Indicators Needed in base.py
- `StreamingBB` (Bollinger Bands - rolling window SMA + std)
- `StreamingRSI` (Wilder's RSI - incremental gain/loss smoothing)
- `StreamingVWAP` (cumulative VWAP with daily reset)
- `StreamingZScore` (rolling zscore of deviation)

These will be added to `strategy/indicators/base.py` alongside the existing streaming classes.

## Files Modified
- `strategy/indicators/base.py` - Add new streaming indicator classes
- `strategy/indicators/__init__.py` - Export new modules
- 8x `strategy/indicators/{name}.py` - New shared cores
- 7x `strategy/strategies/{name}/signal.py` - Refactored to use cores
- 7x `strategy/bitget/{name}/indicator.py` - Refactored to use cores
- 8x `test/indicators/test_{name}_parity.py` - New parity tests

## Total: ~23 new/modified files

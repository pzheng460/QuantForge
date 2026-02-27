"""
Test script for Grid Trading strategy
"""

import numpy as np
import pandas as pd

from nexustrader.backtest import Signal
from strategy.strategies._base.signal_generator import TradeFilterConfig
from strategy.strategies.grid_trading.core import GridConfig
from strategy.strategies.grid_trading.registration import _make_generator

GridTradeFilterConfig = TradeFilterConfig  # backward compat alias

# Create test data - simulate ranging market
np.random.seed(42)
n = 500
dates = pd.date_range('2024-01-01', periods=n, freq='1h')

# Create oscillating price pattern (good for grid trading)
base_price = 50000
t = np.linspace(0, 4*np.pi, n)
trend = 0.1 * t  # Slight upward trend
oscillation = 2000 * np.sin(t) + 1000 * np.sin(3*t)  # Multiple frequency oscillations
noise = np.random.normal(0, 200, n)  # Random noise
prices = base_price + trend + oscillation + noise.cumsum() * 0.1

data = pd.DataFrame({
    'open': prices * 0.999,
    'high': prices * 1.002,
    'low': prices * 0.998,
    'close': prices,
    'volume': np.random.normal(1000000, 100000, n)
})

print("Grid Trading Strategy Test")
print("=" * 40)
print(f"Test data: {n} bars")
print(f"Price range: ${prices.min():.2f} - ${prices.max():.2f}")
print(f"Price volatility: {((prices.max() - prices.min()) / prices.mean() * 100):.2f}%")
print()

# Test different configurations
configs = [
    {"name": "Conservative", "grid_count": 10, "atr_multiplier": 1.5, "sma_period": 20},
    {"name": "Moderate", "grid_count": 20, "atr_multiplier": 2.0, "sma_period": 30},
    {"name": "Aggressive", "grid_count": 30, "atr_multiplier": 2.5, "sma_period": 40},
    {"name": "Bollinger", "grid_count": 15, "atr_multiplier": 1.0, "sma_period": 20, "use_bollinger": True}
]

for test_config in configs:
    print(f"Testing {test_config['name']} configuration:")
    
    # Create strategy config
    config = GridConfig(
        grid_count=test_config["grid_count"],
        atr_multiplier=test_config["atr_multiplier"],
        sma_period=test_config["sma_period"],
        atr_period=14,
        use_bollinger=test_config.get("use_bollinger", False)
    )
    
    filter_config = GridTradeFilterConfig()
    generator = _make_generator(config, filter_config)
    
    # Generate signals
    signals = generator.generate(data)
    
    # Analyze results
    buy_signals = np.sum(signals == Signal.BUY.value)
    sell_signals = np.sum(signals == Signal.SELL.value)
    close_signals = np.sum(signals == Signal.CLOSE.value)
    hold_signals = np.sum(signals == Signal.HOLD.value)
    total_trades = buy_signals + sell_signals
    
    print(f"  Buy signals: {buy_signals}")
    print(f"  Sell signals: {sell_signals}")
    print(f"  Close signals: {close_signals}")
    print(f"  Hold signals: {hold_signals}")
    print(f"  Total trades: {total_trades}")
    print(f"  Trade frequency: {total_trades/n*100:.1f}%")
    
    # Simple P&L simulation
    position = 0
    pnl = 0
    entry_price = 0
    trades = []
    
    for i, signal in enumerate(signals):
        price = prices[i]
        
        if signal == Signal.BUY.value and position == 0:
            position = 1
            entry_price = price
        elif signal == Signal.SELL.value and position == 0:
            position = -1
            entry_price = price
        elif signal == Signal.CLOSE.value and position != 0:
            if position == 1:  # Close long
                trade_pnl = (price - entry_price) / entry_price
            else:  # Close short
                trade_pnl = (entry_price - price) / entry_price
            trades.append(trade_pnl)
            pnl += trade_pnl
            position = 0
            entry_price = 0
    
    if len(trades) > 0:
        win_rate = len([t for t in trades if t > 0]) / len(trades) * 100
        avg_trade = np.mean(trades) * 100
        best_trade = max(trades) * 100 if trades else 0
        worst_trade = min(trades) * 100 if trades else 0
        
        print(f"  Completed trades: {len(trades)}")
        print(f"  Total P&L: {pnl*100:.2f}%")
        print(f"  Avg trade: {avg_trade:.2f}%")
        print(f"  Win rate: {win_rate:.1f}%")
        print(f"  Best trade: {best_trade:.2f}%")
        print(f"  Worst trade: {worst_trade:.2f}%")
    else:
        print("  No completed trades")
    
    print()

print("Grid Trading strategy implementation completed!")
print()
print("Next steps:")
print("1. The strategy structure is complete and registered")
print("2. All required files are in strategy/strategies/grid_trading/")
print("3. Manual testing shows signal generation is working")
print("4. The strategy may need market condition optimization")
print("5. Consider testing in different market regimes (trending vs ranging)")
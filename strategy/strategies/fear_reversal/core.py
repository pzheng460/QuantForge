"""
Core configuration for Fear Reversal strategy.

Exchange-agnostic version: symbols default to empty list.

Strategy logic:
- Entry (min_signals out of 5 must hold):
  1. RSI(14) crosses back above rsi_oversold (mean-reversion signal)
  2. Volume > volume_threshold * SMA(20) volume (volume confirmation)
  3. Price closes above EMA(200) (dynamic support proxy)
  4. ADX < adx_weak_threshold (trend exhaustion / ranging market)
  5. Strong bullish candle: close > open AND body > candle_atr_mult * ATR(14)

- Exit (any one triggers):
  1. RSI > rsi_overbought (take profit)
  2. Price < entry * (1 - stop_loss_pct) (hard stop loss)
  3. Price < peak_price - atr_trail_mult * ATR (ATR trailing stop from peak)
  4. Held for max_holding_bars (time-based exit)

- Long only (no short positions).
"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class FearReversalConfig:
    """Configuration for the Fear Reversal strategy."""

    # Trading pairs (exchange-agnostic: no default)
    symbols: List[str] = field(default_factory=list)

    # Timeframe
    timeframe: str = "1h"

    # RSI indicator
    rsi_period: int = 14
    rsi_oversold: float = 30.0  # RSI cross above this triggers signal 1
    rsi_overbought: float = 70.0  # RSI above this triggers take-profit exit

    # Volatility / trailing stop
    atr_period: int = 14
    atr_trail_mult: float = 1.5  # trailing stop = peak_price - atr_trail_mult * ATR

    # Volume confirmation
    volume_sma_period: int = 20
    volume_threshold: float = 1.5  # volume > SMA * threshold triggers signal 2

    # EMA dynamic support
    ema_support_period: int = 200  # price above EMA200 triggers signal 3

    # ADX weakness (ranging / trend exhaustion)
    adx_period: int = 14
    adx_weak_threshold: float = 20.0  # ADX < this triggers signal 4

    # Bullish candle filter
    candle_atr_mult: float = (
        1.5  # (close - open) > candle_atr_mult * ATR triggers signal 5
    )

    # Entry voting
    min_signals: int = 3  # how many of 5 signals needed to enter long

    # Risk management
    position_size_pct: float = 0.20
    stop_loss_pct: float = 0.02  # 2% hard stop
    max_holding_bars: int = 48  # 48h time-based exit
    daily_loss_limit: float = 0.03

from __future__ import annotations

import statistics
from collections import deque

from pydantic import Field, model_validator

from quantforge.indicators.streaming import StreamingEMA
from quantforge.strategy import StrategyConfig, register_strategy
from quantforge.strategy.bar import BarStrategy, PositionTarget


class EMACrossoverConfig(StrategyConfig):
    fast_period: int = Field(5, ge=3, le=20)
    slow_period: int = Field(13, ge=10, le=50)

    @model_validator(mode="after")
    def periods_are_ordered(self):
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        return self


class _EMACrossoverBase(BarStrategy):
    config_model = EMACrossoverConfig

    def setup(self):
        self.fast = self.add_indicator("ema", self.config.fast_period)
        self.slow = self.add_indicator("ema", self.config.slow_period)

    def on_bar(self, bar):
        if self.fast.crossover(self.slow):
            return PositionTarget(1)
        if self.fast.crossunder(self.slow):
            return PositionTarget(-1)
        return PositionTarget(self.position)


@register_strategy
class EMACrossover(_EMACrossoverBase):
    name = "ema_crossover"


@register_strategy
class EMACrossoverV2(_EMACrossoverBase):
    name = "ema_crossover_v2"
    allocation_pct = 0.78


class EMACrossoverV3Config(EMACrossoverConfig):
    adx_period: int = Field(14, ge=2, le=100)
    adx_threshold: float = Field(20, ge=0, le=100)


@register_strategy
class EMACrossoverV3(BarStrategy):
    name = "ema_crossover_v3"
    allocation_pct = 0.78
    config_model = EMACrossoverV3Config

    def setup(self):
        self.fast = self.add_indicator("ema", self.config.fast_period)
        self.slow = self.add_indicator("ema", self.config.slow_period)
        self.adx = self.add_indicator("adx", self.config.adx_period)

    def on_bar(self, bar):
        trending = self.adx.ready and self.adx.value > self.config.adx_threshold
        if trending and self.fast.crossover(self.slow):
            return PositionTarget(1)
        if trending and self.fast.crossunder(self.slow):
            return PositionTarget(-1)
        return PositionTarget(self.position)


class BBConfig(StrategyConfig):
    bb_period: int = Field(20, ge=2, le=200)
    bb_multiplier: float = Field(2, gt=0, le=10)
    trend_sma_period: int = Field(100, ge=2, le=500)
    stop_loss_pct: float = Field(0.03, gt=0, le=0.5)


@register_strategy
class BollingerBand(BarStrategy):
    name = "bollinger_band"
    config_model = BBConfig

    def setup(self):
        self.bb = self.add_indicator(
            "bb", self.config.bb_period, self.config.bb_multiplier
        )
        self.trend = self.add_indicator("sma", self.config.trend_sma_period)

    def on_bar(self, bar):
        if not self.bb.ready or not self.trend.ready:
            return PositionTarget(self.position)
        if self.position > 0 and bar.close >= self.bb.value:
            return PositionTarget(0)
        if self.position < 0 and bar.close <= self.bb.value:
            return PositionTarget(0)
        if bar.close <= self.bb.lower and bar.close > self.trend.value:
            return PositionTarget(1)
        if bar.close >= self.bb.upper and bar.close < self.trend.value:
            return PositionTarget(-1)
        return PositionTarget(self.position)


class BBV4Config(BBConfig):
    min_bb_width: float = Field(0.015, gt=0, le=1)


@register_strategy
class BollingerBandV4(BollingerBand):
    name = "bollinger_band_v4"
    config_model = BBV4Config

    def on_bar(self, bar):
        if not self.bb.ready or not self.trend.ready or not self.bb.value:
            return PositionTarget(self.position)
        width = (self.bb.upper - self.bb.lower) / self.bb.value
        if self.position > 0:
            if bar.close >= self.bb.value:
                return PositionTarget(0)
            return PositionTarget(
                1, stop_price=bar.close * (1 - self.config.stop_loss_pct)
            )
        if self.position < 0:
            if bar.close <= self.bb.value:
                return PositionTarget(0)
            return PositionTarget(
                -1, stop_price=bar.close * (1 + self.config.stop_loss_pct)
            )
        if width > self.config.min_bb_width:
            if bar.close <= self.bb.lower and bar.close > self.trend.value:
                return PositionTarget(1)
            if bar.close >= self.bb.upper and bar.close < self.trend.value:
                return PositionTarget(-1)
        return PositionTarget(0)


class BBSqueezeConfig(StrategyConfig):
    bb_len: int = Field(20, ge=2, le=200)
    bb_std: float = Field(2, gt=0, le=10)
    ema_fast: int = Field(8, ge=2, le=100)
    ema_slow: int = Field(21, ge=3, le=200)


class _BBSqueezeBase(BarStrategy):
    config_model = BBSqueezeConfig
    defer_exit_on_breakout = False

    def setup(self):
        self.bb = self.add_indicator("bb", self.config.bb_len, self.config.bb_std)
        self.fast = self.add_indicator("ema", self.config.ema_fast)
        self.slow = self.add_indicator("ema", self.config.ema_slow)
        self.widths: deque[float] = deque(maxlen=self.config.bb_len)
        self.previous_squeeze = False

    def on_bar(self, bar):
        if not self.bb.ready or not self.fast.ready or not self.slow.ready:
            return PositionTarget(self.position)
        width = (self.bb.upper - self.bb.lower) / self.bb.value
        avg_width = statistics.fmean(self.widths) if self.widths else width
        squeeze = len(self.widths) == self.widths.maxlen and width < avg_width
        breakout = self.previous_squeeze and not squeeze
        self.widths.append(width)
        self.previous_squeeze = squeeze
        if breakout:
            if self.fast.value > self.slow.value:
                return PositionTarget(1)
            if self.fast.value < self.slow.value:
                return PositionTarget(-1)
        if not (self.defer_exit_on_breakout and breakout):
            if self.position > 0 and bar.close > self.bb.upper:
                return PositionTarget(0)
            if self.position < 0 and bar.close < self.bb.lower:
                return PositionTarget(0)
        return PositionTarget(self.position)


@register_strategy
class BBSqueeze(_BBSqueezeBase):
    name = "bb_squeeze"


@register_strategy
class BBSqueezeV2(_BBSqueezeBase):
    name = "bb_squeeze_v2"
    defer_exit_on_breakout = True


class MomentumADXConfig(StrategyConfig):
    roc_period: int = Field(10, ge=2, le=100)
    ema_fast: int = Field(8, ge=2, le=100)
    ema_slow: int = Field(21, ge=3, le=200)
    ema_trend: int = Field(100, ge=3, le=500)
    adx_period: int = Field(14, ge=2, le=100)
    adx_threshold: float = Field(15, ge=0, le=100)
    atr_period: int = Field(14, ge=2, le=100)
    atr_multiplier: float = Field(2.5, gt=0, le=20)
    roc_threshold: float = -2
    vol_period: int = Field(20, ge=2, le=500)
    vol_threshold: float = Field(0.8, ge=0)


@register_strategy
class MomentumADX(BarStrategy):
    name = "momentum_adx"
    config_model = MomentumADXConfig

    def setup(self):
        self.fast = self.add_indicator("ema", self.config.ema_fast)
        self.slow = self.add_indicator("ema", self.config.ema_slow)
        self.atr = self.add_indicator("atr", self.config.atr_period)

    def on_bar(self, bar):
        if self.fast.crossover(self.slow):
            return PositionTarget(1)
        if self.fast.crossunder(self.slow):
            return PositionTarget(-1)
        if self.position > 0 and self.fast.value < self.slow.value:
            return PositionTarget(0)
        if self.position < 0 and self.fast.value > self.slow.value:
            return PositionTarget(0)
        trail = (
            self.atr.value * self.config.atr_multiplier if self.atr.ready else None
        )
        return PositionTarget(self.position, trailing_distance=trail)


class RSIConfig(StrategyConfig):
    rsi_len: int = Field(14, ge=2, le=100)
    rsi_long: float = Field(55, ge=0, le=100)
    rsi_short: float = Field(45, ge=0, le=100)
    ema_len: int = Field(50, ge=2, le=500)
    adx_len: int = Field(14, ge=2, le=100)
    adx_thresh: float = Field(20, ge=0, le=100)


@register_strategy
class RSIMomentum(BarStrategy):
    name = "rsi_momentum"
    config_model = RSIConfig

    def setup(self):
        self.rsi = self.add_indicator("rsi", self.config.rsi_len)
        self.trend = self.add_indicator("ema", self.config.ema_len)
        self.adx = self.add_indicator("adx", self.config.adx_len)

    def on_bar(self, bar):
        if not self.adx.ready or self.adx.value <= self.config.adx_thresh:
            return PositionTarget(0)
        if self.rsi.ready and self.trend.ready:
            if self.rsi.value > self.config.rsi_long and bar.close > self.trend.value:
                return PositionTarget(1)
            if self.rsi.value < self.config.rsi_short and bar.close < self.trend.value:
                return PositionTarget(-1)
        return PositionTarget(self.position)


class MACDConfig(StrategyConfig):
    fast_len: int = Field(12, ge=2, le=100)
    slow_len: int = Field(26, ge=3, le=200)
    sig_len: int = Field(9, ge=2, le=100)
    adx_len: int = Field(14, ge=2, le=100)
    adx_thresh: float = Field(20, ge=0, le=100)


@register_strategy
class MACDTrend(BarStrategy):
    name = "macd_trend"
    config_model = MACDConfig

    def setup(self):
        self.fast = self.add_indicator("ema", self.config.fast_len)
        self.slow = self.add_indicator("ema", self.config.slow_len)
        self.adx = self.add_indicator("adx", self.config.adx_len)
        self.signal = StreamingEMA(self.config.sig_len)
        self.previous_macd: float | None = None
        self.previous_signal: float | None = None

    def on_bar(self, bar):
        if not self.fast.ready or not self.slow.ready:
            return PositionTarget(self.position)
        macd = self.fast.value - self.slow.value
        signal = self.signal.update(macd)
        if signal is None:
            return PositionTarget(self.position)
        trending = self.adx.ready and self.adx.value > self.config.adx_thresh
        crossed_up = (
            self.previous_macd is not None
            and self.previous_signal is not None
            and self.previous_macd <= self.previous_signal
            and macd > signal
        )
        crossed_down = (
            self.previous_macd is not None
            and self.previous_signal is not None
            and self.previous_macd >= self.previous_signal
            and macd < signal
        )
        self.previous_macd, self.previous_signal = macd, signal
        if not trending:
            return PositionTarget(0)
        if crossed_up:
            return PositionTarget(1)
        if crossed_down:
            return PositionTarget(-1)
        return PositionTarget(self.position)


class SMATrendConfig(StrategyConfig):
    sma_period: int = Field(50, ge=2, le=500)
    atr_period: int = Field(14, ge=2, le=100)
    stop_loss_pct: float = Field(0.05, gt=0, le=0.5)


@register_strategy
class SMATrend(BarStrategy):
    name = "sma_trend"
    timeframe = "1d"
    config_model = SMATrendConfig

    def setup(self):
        self.sma = self.add_indicator("sma", self.config.sma_period)
        self.atr = self.add_indicator("atr", self.config.atr_period)

    def on_bar(self, bar):
        if not self.sma.ready or not self.atr.ready:
            return PositionTarget(self.position)
        if self.position > 0 and bar.close < self.sma.value:
            return PositionTarget(0)
        if bar.close > self.sma.value + self.atr.value * 0.5:
            return PositionTarget(1)
        return PositionTarget(self.position)


class HurstKalmanConfig(StrategyConfig):
    kalman_period: int = Field(20, ge=2, le=200)
    zscore_period: int = Field(20, ge=2, le=200)
    zscore_entry: float = Field(2, gt=0, le=10)
    zscore_exit: float = Field(0.5, ge=0, le=10)
    stop_loss_pct: float = Field(0.03, gt=0, le=0.5)


@register_strategy
class HurstKalman(BarStrategy):
    name = "hurst_kalman"
    config_model = HurstKalmanConfig

    def setup(self):
        self.kalman = self.add_indicator("ema", self.config.kalman_period)
        self.spreads: deque[float] = deque(maxlen=self.config.zscore_period)

    def on_bar(self, bar):
        if not self.kalman.ready:
            return PositionTarget(self.position)
        spread = bar.close - self.kalman.value
        self.spreads.append(spread)
        if len(self.spreads) < self.spreads.maxlen:
            return PositionTarget(self.position)
        mean = statistics.fmean(self.spreads)
        std = statistics.pstdev(self.spreads)
        zscore = (spread - mean) / std if std else 0
        if self.position > 0 and zscore > -self.config.zscore_exit:
            return PositionTarget(0)
        if self.position < 0 and zscore < self.config.zscore_exit:
            return PositionTarget(0)
        if zscore < -self.config.zscore_entry:
            return PositionTarget(1)
        if zscore > self.config.zscore_entry:
            return PositionTarget(-1)
        return PositionTarget(self.position)


class DualRegimeConfig(StrategyConfig):
    adx_period: int = Field(14, ge=2, le=100)
    adx_threshold: float = Field(25, ge=0, le=100)
    ema_fast: int = Field(8, ge=2, le=100)
    ema_slow: int = Field(21, ge=3, le=200)
    ema_trend: int = Field(100, ge=3, le=500)
    roc_period: int = Field(10, ge=2, le=100)
    bb_period: int = Field(20, ge=2, le=200)
    bb_multiplier: float = Field(2, gt=0, le=10)
    atr_period: int = Field(14, ge=2, le=100)
    stop_loss_pct: float = Field(0.03, gt=0, le=0.5)


@register_strategy
class DualRegime(BarStrategy):
    name = "dual_regime"
    config_model = DualRegimeConfig

    def setup(self):
        self.adx = self.add_indicator("adx", self.config.adx_period)
        self.fast = self.add_indicator("ema", self.config.ema_fast)
        self.slow = self.add_indicator("ema", self.config.ema_slow)
        self.trend = self.add_indicator("ema", self.config.ema_trend)
        self.roc = self.add_indicator("roc", self.config.roc_period)
        self.bb = self.add_indicator(
            "bb", self.config.bb_period, self.config.bb_multiplier
        )

    def on_bar(self, bar):
        required = (self.adx, self.fast, self.slow, self.trend, self.roc, self.bb)
        if not all(ind.ready for ind in required):
            return PositionTarget(self.position)
        trending = self.adx.value >= self.config.adx_threshold
        if trending:
            if self.position > 0 and self.fast.value < self.slow.value:
                return PositionTarget(0)
            if self.position < 0 and self.fast.value > self.slow.value:
                return PositionTarget(0)
            if self.roc.value > 0 and self.fast.value > self.slow.value and bar.close > self.trend.value:
                return PositionTarget(1)
            if self.roc.value < 0 and self.fast.value < self.slow.value and bar.close < self.trend.value:
                return PositionTarget(-1)
        else:
            if self.position > 0 and bar.close >= self.bb.value:
                return PositionTarget(0)
            if self.position < 0 and bar.close <= self.bb.value:
                return PositionTarget(0)
            if bar.close <= self.bb.lower:
                return PositionTarget(1)
            if bar.close >= self.bb.upper:
                return PositionTarget(-1)
        return PositionTarget(self.position)

"""Register Fear Reversal strategy with the backtest framework."""

from quantforge.constants import KlineInterval
from strategy.backtest.registry import (
    HeatmapConfig,
    LiveConfig,
    ParityTestConfig,
    StrategyRegistration,
    register_strategy,
)
from strategy.strategies._base.registration_helpers import (
    make_export_config,
    make_filter_config_factory,
    make_mesa_dict_to_config,
    make_split_params_fn,
)
from strategy.strategies._base.signal_generator import (
    BaseSignalGenerator,
    TradeFilterConfig,
)
from strategy.strategies.fear_reversal.core import FearReversalConfig
from strategy.strategies.fear_reversal.signal_core import FearReversalSignalCore

# Custom columns tuple: fear_reversal needs open price for candle body check
COLUMNS_CLOSE_HIGH_LOW_VOLUME_OPEN = ("close", "high", "low", "volume", "open")


def _pre_signal_hook(strategy, symbol, signal, price, indicator, current_bar):
    """Log signal breakdown every bar."""
    core = indicator.core
    if not hasattr(core, "get_signal_breakdown"):
        return False
    try:
        vol = indicator._last_volume if hasattr(indicator, "_last_volume") else 0
        bd = core.get_signal_breakdown(price, vol)
        marks = []
        marks.append(f"RSI={'✓' if bd['rsi_reversal'] else '✗'}({bd['rsi']:.1f})" if bd['rsi'] is not None else "RSI=N/A")
        marks.append(f"Vol={'✓' if bd['vol_ok'] else '✗'}")
        marks.append(f"EMA200={'✓' if bd['above_ema'] else '✗'}({bd['ema200']:.0f})" if bd['ema200'] is not None else "EMA200=N/A")
        marks.append(f"ADX={'✓' if bd['adx_weak'] else '✗'}({bd['adx']:.1f})" if bd['adx'] is not None else "ADX=✓(N/A)")
        marks.append(f"Candle={'✓' if bd['strong_candle'] else '✗'}")
        strategy.log.info(
            f"{symbol} | Signals {bd['count']}/{bd['needed']}: {' | '.join(marks)}"
        )
    except Exception:
        pass
    return False


def _make_generator(config, filter_config):
    return BaseSignalGenerator(
        config,
        filter_config,
        core_cls=FearReversalSignalCore,
        update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME_OPEN,
    )


register_strategy(
    StrategyRegistration(
        name="fear_reversal",
        display_name="Fear Reversal",
        signal_generator_cls=_make_generator,
        config_cls=FearReversalConfig,
        filter_config_cls=TradeFilterConfig,
        default_interval=KlineInterval.HOUR_1,
        default_grid={
            "rsi_oversold": [25, 30, 35],
            "volume_threshold": [1.2, 1.5, 2.0],
            "min_signals": [2, 3, 4],
            "atr_trail_mult": [1.0, 1.5, 2.0],
            "adx_weak_threshold": [15, 20, 25],
        },
        heatmap_config=HeatmapConfig(
            x_param_name="rsi_oversold",
            y_param_name="volume_threshold",
            x_range=(20, 45),
            y_range=(1.0, 2.5),
            x_label="RSI Oversold",
            y_label="Volume Threshold",
            third_param_choices={
                "min_signals": [2, 3, 4],
                "atr_trail_mult": [1.0, 1.5, 2.0],
                "adx_weak_threshold": [15.0, 20.0, 25.0],
                "candle_atr_mult": [1.0, 1.5, 2.0],
            },
            fixed_params={
                "rsi_period": 14,
                "rsi_overbought": 70.0,
                "atr_period": 14,
                "volume_sma_period": 20,
                "ema_support_period": 200,
                "adx_period": 14,
                "position_size_pct": 0.20,
                "stop_loss_pct": 0.02,
                "max_holding_bars": 48,
                "daily_loss_limit": 0.03,
            },
            filter_config_factory=make_filter_config_factory(TradeFilterConfig),
        ),
        default_filter_kwargs={},
        split_params_fn=make_split_params_fn(FearReversalConfig),
        mesa_dict_to_config_fn=make_mesa_dict_to_config(
            FearReversalConfig,
            TradeFilterConfig,
            "rsi_oversold",
            "volume_threshold",
            x_label="RSI Oversold",
            y_label="Volume Threshold",
        ),
        export_config_fn=make_export_config(
            "fear_reversal",
            FearReversalConfig,
            TradeFilterConfig,
            "strategy.strategies.fear_reversal.core",
            "strategy.strategies._base.signal_generator",
        ),
        live_config=LiveConfig(
            core_cls=FearReversalSignalCore,
            update_columns=COLUMNS_CLOSE_HIGH_LOW_VOLUME_OPEN,
            warmup_fn=lambda cfg: (
                max(
                    cfg.ema_support_period,
                    cfg.rsi_period,
                    cfg.atr_period + 1,
                    cfg.volume_sma_period,
                    cfg.adx_period * 2 + 1,
                )
                + 10
            ),
            use_dual_mode=True,
            enable_stale_guard=True,
            max_kline_age_s=120.0,
            pre_signal_hook_fn=_pre_signal_hook,
        ),
        parity_config=ParityTestConfig(
            custom_config_kwargs={
                "rsi_period": 10,
                "rsi_oversold": 35.0,
                "rsi_overbought": 65.0,
                "atr_period": 10,
                "atr_trail_mult": 2.0,
                "volume_sma_period": 15,
                "volume_threshold": 1.2,
                "ema_support_period": 50,
                "adx_period": 10,
                "adx_weak_threshold": 25.0,
                "candle_atr_mult": 1.0,
                "min_signals": 2,
                "stop_loss_pct": 0.05,
                "max_holding_bars": 30,
            },
            custom_filter_kwargs={
                "min_holding_bars": 3,
                "cooldown_bars": 1,
                "signal_confirmation": 1,
            },
            trades_config_kwargs={
                "rsi_period": 7,
                "rsi_oversold": 40.0,
                "rsi_overbought": 60.0,
                "atr_period": 7,
                "atr_trail_mult": 2.0,
                "volume_sma_period": 10,
                "volume_threshold": 1.0,
                "ema_support_period": 30,
                "adx_period": 7,
                "adx_weak_threshold": 30.0,
                "candle_atr_mult": 0.5,
                "min_signals": 2,
                "stop_loss_pct": 0.05,
                "max_holding_bars": 20,
            },
            trades_filter_kwargs={
                "min_holding_bars": 2,
                "cooldown_bars": 1,
                "signal_confirmation": 1,
            },
            trades_data_size=2000,
        ),
    )
)

"""EMA 5/13 backtest for full year 2024 (BTC/USDT:USDT, 1h, Bitget)."""

import asyncio
from datetime import datetime

from quantforge.constants import KlineInterval
from strategy.backtest.utils import fetch_data


async def main():
    start = datetime(2023, 1, 1)
    end = datetime(2024, 1, 1)

    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=start,
        end_date=end,
        interval=KlineInterval.HOUR_1,
        exchange="bitget",
    )
    print(f"Data: {len(data)} bars, {data.index[0]} → {data.index[-1]}")

    from strategy.backtest.runner import BacktestRunner
    from strategy.strategies.ema_crossover.core import EMAConfig
    from strategy.strategies._base.signal_generator import TradeFilterConfig

    config = EMAConfig(
        fast_period=5,
        slow_period=13,
        stop_loss_pct=0.05,
    )
    filter_config = TradeFilterConfig(
        min_holding_bars=4,
        cooldown_bars=2,
        signal_confirmation=1,
    )

    runner = BacktestRunner(
        strategy_name="ema_crossover",
        exchange="bitget",
        leverage=5,
    )

    results = runner.run_single(
        data=data,
        config_override=config,
        filter_override=filter_config,
    )


asyncio.run(main())

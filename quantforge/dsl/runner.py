"""Live runner — bridge declarative Strategy API to existing GenericStrategy.

Usage:
    from quantforge.dsl.runner import deploy
    deploy(EMACross, exchange="bitget", demo=True)

    # Or from CLI:
    python -m quantforge.dsl.runner --strategy ema_crossover --exchange bitget --demo
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from quantforge.dsl.api import Strategy


def _bridge_to_legacy(
    strategy_cls: type[Strategy],
    **param_overrides,
):
    """Create a legacy StrategyRegistration-compatible config from a declarative Strategy.

    Returns (core_cls, config, filter_config) that can be used
    with the existing GenericStrategy system.
    """
    from dataclasses import dataclass, field
    from typing import List

    from strategy.strategies._base.signal_core_base import (
        HOLD,
        BaseSignalCore,
    )
    from strategy.strategies._base.signal_generator import TradeFilterConfig

    # Build a config dataclass dynamically
    config_defaults = {}
    for name, param in strategy_cls._get_params().items():
        val = param_overrides.get(name, param.default)
        config_defaults[name] = val

    # Add standard fields
    config_defaults.setdefault("stop_loss_pct", 0.05)
    config_defaults.setdefault("position_size_pct", 0.10)

    @dataclass
    class DynamicConfig:
        symbols: List[str] = field(default_factory=list)
        timeframe: str = strategy_cls.timeframe or "15m"

    config = DynamicConfig()
    for k, v in config_defaults.items():
        setattr(config, k, v)

    # Build a SignalCore that wraps the new Strategy
    class BridgeSignalCore(BaseSignalCore):
        def __init__(
            self, cfg, min_holding_bars=4, cooldown_bars=2, signal_confirmation=1
        ):
            super().__init__(cfg, min_holding_bars, cooldown_bars, signal_confirmation)
            self._strat = strategy_cls(**param_overrides)

        def update(self, close, high=None, low=None, volume=None):
            from quantforge.dsl.api import Bar

            bar = Bar(
                open=close,
                high=high if high is not None else close,
                low=low if low is not None else close,
                close=close,
                volume=volume if volume is not None else 0.0,
            )
            signal = self._strat._process_bar(bar)
            self.bar_index += 1
            return signal

        def update_indicators_only(self, close, high=None, low=None, volume=None):
            from quantforge.dsl.api import Bar

            bar = Bar(
                open=close,
                high=high if high is not None else close,
                low=low if low is not None else close,
                close=close,
                volume=volume if volume is not None else 0.0,
            )
            for ind in self._strat._indicators:
                ind._update(bar)
            self._strat._bar_index += 1
            self.bar_index += 1

        def get_raw_signal(self):
            return HOLD

        def reset(self):
            self._strat.reset()
            self._reset_position_state()

    filter_config = TradeFilterConfig(
        min_holding_bars=4,
        cooldown_bars=2,
        signal_confirmation=1,
    )

    return BridgeSignalCore, config, filter_config


def deploy(
    strategy_cls: type[Strategy],
    exchange: str = "bitget",
    demo: bool = True,
    symbol: str = "BTCUSDT-PERP",
    mesa_index: int = 0,
):
    """Deploy a declarative Strategy for live trading.

    Bridges the new Strategy API to the existing GenericStrategy system.
    """
    strategy_name = strategy_cls.name or strategy_cls.__name__.lower()

    print(f"Deploying {strategy_name} on {exchange} ({'demo' if demo else 'live'})")
    print(f"Symbol: {symbol}")
    print(f"Timeframe: {strategy_cls.timeframe}")
    print(
        f"\nTo deploy via existing runner:\n"
        f"  uv run python -m strategy.runner -S {strategy_name} --mesa {mesa_index} "
        f"--exchange {exchange}"
    )


def main():
    """CLI entry point."""
    import argparse

    from quantforge.dsl.registry import _REGISTRY, list_strategies

    parser = argparse.ArgumentParser(description="Declarative Strategy Runner")
    parser.add_argument("-S", "--strategy", help="Strategy name")
    parser.add_argument(
        "--exchange", default="bitget", help="Exchange (default: bitget)"
    )
    parser.add_argument(
        "--demo", action="store_true", default=True, help="Use demo mode"
    )
    parser.add_argument("--symbol", default="BTCUSDT-PERP", help="Trading symbol")
    parser.add_argument("--list", action="store_true", help="List available strategies")

    args = parser.parse_args()

    if args.list:
        names = list_strategies()
        if not names:
            print("No strategies registered. Import strategy modules first.")
            return
        print("Available declarative strategies:")
        for n in names:
            cls = _REGISTRY[n]
            doc = (cls.__doc__ or "").strip().split("\n")[0]
            print(f"  {n:30s} {doc}")
        return

    if not args.strategy:
        parser.print_help()
        return

    # Import examples to trigger registration
    try:
        import quantforge.dsl.examples  # noqa: F401
    except ImportError:
        pass

    from quantforge.dsl.registry import get_strategy

    cls = get_strategy(args.strategy)
    deploy(cls, exchange=args.exchange, demo=args.demo, symbol=args.symbol)


if __name__ == "__main__":
    main()

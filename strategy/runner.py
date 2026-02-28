"""
Generic CLI runner for any registered strategy with a LiveConfig.

Usage:
    # Run with default settings (Bitget demo):
    uv run python -m strategy.runner --strategy ema_crossover --mesa 0

    # Specify exchange:
    uv run python -m strategy.runner --strategy bollinger_band --mesa 1 --exchange bitget

    # Custom symbol:
    uv run python -m strategy.runner --strategy hurst_kalman --symbol ETHUSDT-PERP.BITGET

    # List available strategies:
    uv run python -m strategy.runner --list

    # List mesa configs for a strategy:
    uv run python -m strategy.runner --strategy ema_crossover --list-configs
"""

import argparse
import sys
from pathlib import Path


def _build_exchange_config(exchange: str, demo: bool):
    """Build exchange-specific engine configuration components.

    Returns (exchange_type, account_type, basic_config_dict,
             public_conn_configs, private_conn_configs, is_testnet).
    """
    from nexustrader.config import (
        BasicConfig,
        PrivateConnectorConfig,
        PublicConnectorConfig,
    )
    from nexustrader.constants import ExchangeType, settings

    exchange = exchange.lower()

    if exchange == "bitget":
        from nexustrader.exchange import BitgetAccountType

        if demo:
            account_type = BitgetAccountType.UTA_DEMO
            api_key = settings.BITGET.DEMO.API_KEY
            secret = settings.BITGET.DEMO.SECRET
            passphrase = settings.BITGET.DEMO.PASSPHRASE
            testnet = True
        else:
            account_type = BitgetAccountType.UTA
            api_key = settings.BITGET.API_KEY
            secret = settings.BITGET.SECRET
            passphrase = settings.BITGET.PASSPHRASE
            testnet = False

        exchange_type = ExchangeType.BITGET
        basic = BasicConfig(
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            testnet=testnet,
        )
    elif exchange == "binance":
        from nexustrader.exchange import BinanceAccountType

        if demo:
            account_type = BinanceAccountType.USD_M_FUTURE_TESTNET
            api_key = settings.BINANCE.TESTNET.API_KEY
            secret = settings.BINANCE.TESTNET.SECRET
            testnet = True
        else:
            account_type = BinanceAccountType.USD_M_FUTURE
            api_key = settings.BINANCE.API_KEY
            secret = settings.BINANCE.SECRET
            testnet = False

        exchange_type = ExchangeType.BINANCE
        basic = BasicConfig(
            api_key=api_key,
            secret=secret,
            testnet=testnet,
        )
    elif exchange == "bybit":
        from nexustrader.exchange import BybitAccountType

        if demo:
            account_type = BybitAccountType.CONTRACT_TESTNET
            api_key = settings.BYBIT.TESTNET.API_KEY
            secret = settings.BYBIT.TESTNET.SECRET
            testnet = True
        else:
            account_type = BybitAccountType.CONTRACT
            api_key = settings.BYBIT.API_KEY
            secret = settings.BYBIT.SECRET
            testnet = False

        exchange_type = ExchangeType.BYBIT
        basic = BasicConfig(
            api_key=api_key,
            secret=secret,
            testnet=testnet,
        )
    elif exchange == "okx":
        from nexustrader.exchange import OkxAccountType

        if demo:
            account_type = OkxAccountType.DEMO
            api_key = settings.OKX.DEMO.API_KEY
            secret = settings.OKX.DEMO.SECRET
            passphrase = settings.OKX.DEMO.PASSPHRASE
            testnet = True
        else:
            account_type = OkxAccountType.LIVE
            api_key = settings.OKX.API_KEY
            secret = settings.OKX.SECRET
            passphrase = settings.OKX.PASSPHRASE
            testnet = False

        exchange_type = ExchangeType.OKX
        basic = BasicConfig(
            api_key=api_key,
            secret=secret,
            passphrase=passphrase,
            testnet=testnet,
        )
    else:
        raise ValueError(
            f"Unsupported exchange: {exchange}. Supported: bitget, binance, bybit, okx"
        )

    public_conns = [
        PublicConnectorConfig(
            account_type=account_type,
            enable_rate_limit=True,
        )
    ]
    private_conns = [
        PrivateConnectorConfig(
            account_type=account_type,
            enable_rate_limit=True,
            leverage=5,
        )
    ]

    return exchange_type, account_type, basic, public_conns, private_conns


def _resolve_symbol(symbol: str, exchange: str) -> str:
    """Resolve symbol for the given exchange if not fully qualified."""
    if "." in symbol:
        return symbol
    suffix = exchange.upper()
    return f"{symbol}.{suffix}"


def main():
    parser = argparse.ArgumentParser(
        description="Generic runner for registered trading strategies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-S",
        "--strategy",
        type=str,
        help="Strategy name (e.g. ema_crossover, bollinger_band, hurst_kalman)",
    )
    parser.add_argument(
        "-m",
        "--mesa",
        type=int,
        default=0,
        help="Mesa config index (0=best, default: 0)",
    )
    parser.add_argument(
        "-X",
        "--exchange",
        type=str,
        default="bitget",
        help="Exchange (bitget, binance, bybit, okx). Default: bitget",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Trading symbol (default: from LiveConfig or BTCUSDT-PERP.{EXCHANGE})",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=True,
        help="Use demo/testnet account (default: True)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Use live/mainnet account (overrides --demo)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all registered strategies with LiveConfig",
    )
    parser.add_argument(
        "--list-configs",
        action="store_true",
        help="List Mesa configs for the given strategy",
    )

    args = parser.parse_args()

    # Ensure strategy registrations are loaded
    import strategy.strategies  # noqa: F401

    if args.list:
        from strategy.backtest.registry import list_strategies, get_strategy as _get

        print("Registered strategies with generic runner support:")
        print("-" * 60)
        for name in list_strategies():
            reg = _get(name)
            has_live = "YES" if reg.live_config else "no (custom live.py)"
            print(f"  {name:<25} {reg.display_name:<35} Generic: {has_live}")
        return

    if not args.strategy:
        parser.error("--strategy is required (or use --list)")

    if args.list_configs:
        from strategy.strategies._base.generic_configs import list_configs

        list_configs(args.strategy)
        return

    # --- Load strategy registration ---
    from strategy.backtest.registry import get_strategy

    reg = get_strategy(args.strategy)
    if reg.live_config is None:
        print(
            f"Error: Strategy '{args.strategy}' does not have a LiveConfig. "
            f"Use the strategy's custom live.py instead:\n"
            f"  uv run python -m strategy.strategies.{args.strategy}.live"
        )
        sys.exit(1)

    # --- Load config ---
    from strategy.strategies._base.generic_configs import get_config

    is_demo = args.demo and not args.live

    try:
        selected = get_config(args.strategy, args.mesa)
        strategy_config, filter_config = selected.get_configs()
    except (FileNotFoundError, ValueError) as e:
        print(f"Config load info: {e}")
        print(f"Using default {reg.display_name} config.")
        from strategy.backtest.config import StrategyConfig as _SC

        strategy_config = reg.config_cls()
        filter_config = reg.filter_config_cls()
        selected = _SC(
            name="Default",
            description=f"Default {reg.display_name} config",
            strategy_config=strategy_config,
            filter_config=filter_config,
        )

    # --- Build exchange config ---
    exchange = args.exchange.lower()
    exchange_type, account_type, basic, pub_conns, priv_conns = _build_exchange_config(
        exchange, is_demo
    )

    # --- Resolve symbol ---
    if args.symbol:
        symbol = _resolve_symbol(args.symbol, exchange)
    else:
        default = reg.live_config.default_symbol
        if exchange != "bitget" and ".BITGET" in default:
            default = default.replace(".BITGET", f".{exchange.upper()}")
        symbol = default
    symbols = [symbol]

    # --- Set up logging ---
    from strategy.strategies._base.base_strategy import LogTee

    log_dir = Path(__file__).parent / "strategies" / args.strategy
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "strategy_output.log"
    sys.stdout = LogTee(str(log_file))

    engine_log_file = log_dir / f"{args.strategy}.log"
    if engine_log_file.exists():
        engine_log_file.write_text("")

    # --- Create strategy ---
    from strategy.strategies._base.generic_strategy import GenericStrategy

    strat = GenericStrategy(
        strategy_name=args.strategy,
        symbols=symbols,
        config=strategy_config,
        filter_config=filter_config,
        account_type=account_type,
    )
    strat.set_config_info(args.mesa, selected.name)

    # --- Build engine ---
    from nexustrader.config import Config, LogConfig
    from nexustrader.engine import Engine

    config = Config(
        strategy_id=f"{args.strategy}_{selected.name.lower().replace(' ', '_')}",
        user_id="user_test",
        strategy=strat,
        log_config=LogConfig(
            level_stdout="INFO",
            level_file="INFO",
            directory=str(log_dir),
            file_name=f"{args.strategy}.log",
        ),
        basic_config={exchange_type: basic},
        public_conn_config={exchange_type: pub_conns},
        private_conn_config={exchange_type: priv_conns},
    )

    engine = Engine(config)

    mode_str = "DEMO" if is_demo else "LIVE"
    print(f"Strategy: {reg.display_name}")
    print(f"Config:   Mesa #{args.mesa} ({selected.name})")
    print(f"Exchange: {exchange.upper()} ({mode_str})")
    print(f"Symbol:   {symbol}")
    print(f"Log file: {log_file}")
    print("=" * 60)

    try:
        engine.start()
    finally:
        engine.dispose()
        if hasattr(sys.stdout, "close"):
            sys.stdout.close()


if __name__ == "__main__":
    main()

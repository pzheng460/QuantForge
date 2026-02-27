"""
Grid Trading Strategy - Main Entry Point

Launch the grid trading strategy for Bitget paper trading.

Usage:
    # Show help
    uv run python -m strategy.live.grid --help
    
    # Run strategy (default settings)
    uv run python -m strategy.live.grid
    
    # Run in background with tmux
    tmux new-session -d -s grid "cd /home/pzheng46/NexusTrader && uv run python -m strategy.live.grid"
"""

import argparse
import sys
from pathlib import Path

from nexustrader.config import (
    BasicConfig,
    Config,
    LogConfig,
    PrivateConnectorConfig,
    PublicConnectorConfig,
)
from nexustrader.constants import ExchangeType, settings
from nexustrader.engine import Engine
from nexustrader.exchange import BitgetAccountType

from strategy.live.grid.strategy import create_strategy


def main():
    """Main entry point for grid trading strategy."""
    parser = argparse.ArgumentParser(
        description="Grid Trading Strategy for Bitget Paper Trading",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run strategy with default settings
  uv run python -m strategy.live.grid
  
  # Run in background with tmux
  tmux new-session -d -s grid "cd /home/pzheng46/NexusTrader && uv run python -m strategy.live.grid 2>&1 | tee /tmp/grid_start.log"

Grid Strategy Parameters:
  - Grid Count: 3 levels (0-3)
  - ATR Multiplier: 3.0x for grid range
  - SMA Period: 20 bars for center calculation
  - ATR Period: 14 bars for volatility
  - Recalc Period: 24 bars (daily recalculation)
  - Entry Lines: 1 (enter when price moves 1+ grid lines)
  - Profit Lines: 2 (exit when price reverses 2+ grid lines)
  - Stop Loss: 3% hard stop
  - Leverage: 5x
  - Position Size: 20% of account per trade

Trading Logic:
  - FLAT → BUY when price drops ≥1 line AND level ≤1 (lower half)
  - FLAT → SELL when price rises ≥1 line AND level ≥1 (upper half) 
  - LONG → CLOSE when price rises ≥2 lines from trough
  - SHORT → CLOSE when price falls ≥2 lines from peak
        """
    )
    
    parser.add_argument(
        "--symbol",
        default="BTCUSDT-PERP.BITGET",
        help="Trading symbol (default: BTCUSDT-PERP.BITGET)"
    )
    
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )
    
    parser.add_argument(
        "--log-dir", 
        default=str(Path(__file__).parent),
        help="Log directory (default: strategy directory)"
    )

    args = parser.parse_args()

    # Display strategy info
    print("=" * 80)
    print("NEXUSTRADER - GRID TRADING STRATEGY")
    print("=" * 80)
    print(f"Symbol: {args.symbol}")
    print(f"Exchange: Bitget (Paper Trading)")
    print(f"Timeframe: 1 Hour")
    print(f"Log Level: {args.log_level}")
    print(f"Log Directory: {args.log_dir}")
    print()
    print("Strategy Parameters:")
    print("  Grid Count: 3 levels (0-3)")
    print("  ATR Multiplier: 3.0x")
    print("  SMA Period: 20 bars")
    print("  ATR Period: 14 bars") 
    print("  Recalc Period: 24 bars (daily)")
    print("  Entry Threshold: 1 grid line")
    print("  Profit Target: 2 grid lines")
    print("  Stop Loss: 3%")
    print("  Leverage: 5x")
    print("  Position Size: 20% per trade")
    print()
    print("Press Ctrl+C to stop the strategy")
    print("=" * 80)

    # Verify API credentials
    try:
        api_key = settings.BITGET.DEMO.API_KEY
        secret = settings.BITGET.DEMO.SECRET  
        passphrase = settings.BITGET.DEMO.PASSPHRASE
        
        if not all([api_key, secret, passphrase]):
            print("ERROR: Missing API credentials in .keys/.secrets.toml")
            print("Required: BITGET.DEMO.API_KEY, SECRET, PASSPHRASE")
            sys.exit(1)
            
        print(f"✓ API Key: {api_key[:8]}***")
        print(f"✓ Credentials loaded from .keys/.secrets.toml")
        print()
        
    except Exception as e:
        print(f"ERROR: Failed to load API credentials: {e}")
        print("Make sure .keys/.secrets.toml exists with BITGET.DEMO section")
        sys.exit(1)

    # Create strategy
    strategy = create_strategy()
    
    # Configure engine
    config = Config(
        strategy_id="grid_trading_live",
        user_id="user_grid",
        strategy=strategy,
        log_config=LogConfig(
            level_stdout=args.log_level,
            level_file=args.log_level,
            directory=args.log_dir,
            file_name="grid.log",
        ),
        basic_config={
            ExchangeType.BITGET: BasicConfig(
                api_key=api_key,
                secret=secret,
                passphrase=passphrase,
                testnet=True,  # Paper trading
            )
        },
        public_conn_config={
            ExchangeType.BITGET: [
                PublicConnectorConfig(
                    account_type=BitgetAccountType.UTA_DEMO,
                    enable_rate_limit=True,
                )
            ]
        },
        private_conn_config={
            ExchangeType.BITGET: [
                PrivateConnectorConfig(
                    account_type=BitgetAccountType.UTA_DEMO,
                    enable_rate_limit=True,
                    leverage=5,
                )
            ]
        },
    )

    # Create engine
    engine = Engine(config)
    
    try:
        print("Starting Grid Trading Strategy...")
        print("Warmup phase: ~30 minutes (need 30+ bars for SMA/ATR)")
        print("Live trading will begin after warmup + 5s settle period")
        print()
        
        engine.start()
        
    except KeyboardInterrupt:
        print("\nStrategy stopped by user")
    except Exception as e:
        print(f"\nStrategy failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("Shutting down engine...")
        engine.dispose()
        print("Grid Trading Strategy stopped.")


if __name__ == "__main__":
    main()
"""
Script to set Bitget position mode to one-way.

Usage:
    uv run python -m strategy.bitget.hurst_kalman.set_position_mode
"""

import ccxt

from nexustrader.constants import settings

# Get credentials from settings
API_KEY = settings.BITGET.DEMO.API_KEY
SECRET = settings.BITGET.DEMO.SECRET
PASSPHRASE = settings.BITGET.DEMO.PASSPHRASE


def main():
    print("=" * 60)
    print("Setting Bitget Position Mode to One-Way")
    print("=" * 60)

    exchange = ccxt.bitget({
        'apiKey': API_KEY,
        'secret': SECRET,
        'password': PASSPHRASE,
        'options': {
            'defaultType': 'swap',
            'sandboxMode': True,  # Demo environment
        }
    })

    try:
        # First check current position mode
        print("\nChecking current account status...")

        # Try to set position mode to one-way (hedged=False)
        print("Setting position mode to one-way...")
        result = exchange.set_position_mode(hedged=False, symbol='BTCUSDT')
        print(f"Result: {result}")
        print("\n✅ Position mode changed to one-way successfully!")

    except ccxt.ExchangeError as e:
        error_msg = str(e)
        if "position mode" in error_msg.lower() or "already" in error_msg.lower():
            print(f"\n⚠️ Position mode may already be set or cannot be changed via API.")
            print(f"Error: {e}")
            print("\nPlease change position mode manually in Bitget web interface:")
            print("1. Go to https://www.bitget.com/")
            print("2. Navigate to Futures > Settings")
            print("3. Change Position Mode from 'Hedge' to 'One-Way'")
        else:
            print(f"\n❌ Error: {e}")

    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")


if __name__ == "__main__":
    main()

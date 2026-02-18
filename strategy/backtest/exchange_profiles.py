"""
Exchange Profiles.

Pre-defined fee/slippage profiles for supported exchanges, used by the
unified backtest framework to parameterize cost models and data fetching.
"""

from dataclasses import dataclass
from typing import Dict

from nexustrader.backtest import CostConfig


@dataclass
class ExchangeProfile:
    """Fee and symbol configuration for an exchange."""

    name: str  # e.g. "Binance"
    ccxt_id: str  # e.g. "binance"
    default_symbol: str  # e.g. "BTC/USDT:USDT"
    nexus_symbol_suffix: str  # e.g. ".BINANCE"
    maker_fee: float
    taker_fee: float
    slippage_pct: float = 0.0005

    def cost_config(self, use_funding_rate: bool = True) -> CostConfig:
        """Create a CostConfig from this profile."""
        return CostConfig(
            maker_fee=self.maker_fee,
            taker_fee=self.taker_fee,
            slippage_pct=self.slippage_pct,
            use_funding_rate=use_funding_rate,
        )

    def nexus_symbol(self, base: str = "BTCUSDT-PERP") -> str:
        """Build a NexusTrader symbol string."""
        return f"{base}{self.nexus_symbol_suffix}"


# ---------------------------------------------------------------------------
# Pre-defined profiles
# ---------------------------------------------------------------------------
PROFILES: Dict[str, ExchangeProfile] = {
    "bitget": ExchangeProfile(
        name="Bitget",
        ccxt_id="bitget",
        default_symbol="BTC/USDT:USDT",
        nexus_symbol_suffix=".BITGET",
        maker_fee=0.0002,
        taker_fee=0.0005,
    ),
    "binance": ExchangeProfile(
        name="Binance",
        ccxt_id="binance",
        default_symbol="BTC/USDT:USDT",
        nexus_symbol_suffix=".BINANCE",
        maker_fee=0.0002,
        taker_fee=0.0004,
    ),
    "okx": ExchangeProfile(
        name="OKX",
        ccxt_id="okx",
        default_symbol="BTC/USDT:USDT",
        nexus_symbol_suffix=".OKX",
        maker_fee=0.0002,
        taker_fee=0.0005,
    ),
    "bybit": ExchangeProfile(
        name="Bybit",
        ccxt_id="bybit",
        default_symbol="BTC/USDT:USDT",
        nexus_symbol_suffix=".BYBIT",
        maker_fee=0.0002,
        taker_fee=0.0005,
    ),
    "hyperliquid": ExchangeProfile(
        name="Hyperliquid",
        ccxt_id="hyperliquid",
        default_symbol="BTC/USDT:USDT",
        nexus_symbol_suffix=".HYPERLIQUID",
        maker_fee=0.0002,
        taker_fee=0.0005,
    ),
}


def get_profile(exchange: str) -> ExchangeProfile:
    """Get an exchange profile by name (case-insensitive).

    Raises:
        KeyError: If the exchange is not supported.
    """
    key = exchange.lower()
    if key not in PROFILES:
        available = ", ".join(PROFILES.keys())
        raise KeyError(
            f"Exchange '{exchange}' is not supported. Available: {available}"
        )
    return PROFILES[key]


def list_exchanges() -> list:
    """Return names of all supported exchanges."""
    return list(PROFILES.keys())

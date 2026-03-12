from quantforge.exchange.binance.constants import BinanceAccountType
from quantforge.exchange.binance.exchange import BinanceExchangeManager
from quantforge.exchange.binance.connector import (
    BinancePublicConnector,
    BinancePrivateConnector,
)
from quantforge.exchange.binance.rest_api import BinanceApiClient
from quantforge.exchange.binance.ems import BinanceExecutionManagementSystem
from quantforge.exchange.binance.oms import BinanceOrderManagementSystem
from quantforge.exchange.binance.factory import BinanceFactory

# Auto-register factory on import
try:
    from quantforge.exchange.registry import register_factory

    register_factory(BinanceFactory())
except ImportError:
    # Registry not available yet during bootstrap
    pass

__all__ = [
    "BinanceAccountType",
    "BinanceExchangeManager",
    "BinancePublicConnector",
    "BinancePrivateConnector",
    "BinanceHttpClient",
    "BinanceApiClient",
    "BinanceExecutionManagementSystem",
    "BinanceOrderManagementSystem",
    "BinanceFactory",
]

from quantforge.exchange.hyperliquid.exchange import HyperLiquidExchangeManager
from quantforge.exchange.hyperliquid.constants import HyperLiquidAccountType
from quantforge.exchange.hyperliquid.connector import (
    HyperLiquidPublicConnector,
    HyperLiquidPrivateConnector,
)
from quantforge.exchange.hyperliquid.oms import HyperLiquidOrderManagementSystem
from quantforge.exchange.hyperliquid.ems import HyperLiquidExecutionManagementSystem
from quantforge.exchange.hyperliquid.factory import HyperLiquidFactory

# Auto-register factory on import
try:
    from quantforge.exchange.registry import register_factory

    register_factory(HyperLiquidFactory())
except ImportError:
    # Registry not available yet during bootstrap
    pass

__all__ = [
    "HyperLiquidExchangeManager",
    "HyperLiquidAccountType",
    "HyperLiquidPublicConnector",
    "HyperLiquidPrivateConnector",
    "HyperLiquidOrderManagementSystem",
    "HyperLiquidExecutionManagementSystem",
    "HyperLiquidFactory",
]

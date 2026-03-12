from quantforge.exchange.bitget.exchange import BitgetExchangeManager
from quantforge.exchange.bitget.connector import (
    BitgetPublicConnector,
    BitgetPrivateConnector,
)
from quantforge.exchange.bitget.constants import BitgetAccountType
from quantforge.exchange.bitget.ems import BitgetExecutionManagementSystem
from quantforge.exchange.bitget.oms import BitgetOrderManagementSystem
from quantforge.exchange.bitget.factory import BitgetFactory

# Auto-register factory on import
try:
    from quantforge.exchange.registry import register_factory

    register_factory(BitgetFactory())
except ImportError:
    # Registry not available yet during bootstrap
    pass

__all__ = [
    "BitgetExchangeManager",
    "BitgetPublicConnector",
    "BitgetPrivateConnector",
    "BitgetAccountType",
    "BitgetExecutionManagementSystem",
    "BitgetOrderManagementSystem",
    "BitgetFactory",
]

"""
Trading cost model for backtesting.

Simulates realistic trading costs including:
- Maker/taker fees
- Slippage (fixed and ATR-based)
- Funding rates for perpetual contracts
- Limit order fill probability
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class CostConfig:
    """Configuration for trading costs."""

    maker_fee: float = 0.0002  # 0.02%
    taker_fee: float = 0.0005  # 0.05%
    slippage_pct: float = 0.0005  # 0.05%
    use_funding_rate: bool = True
    atr_slippage_multiplier: float = 0.0  # Additional ATR-based slippage


class CostModel:
    """
    Model for calculating trading costs.

    Supports:
    - Fixed percentage fees (maker/taker)
    - Fixed and ATR-based slippage
    - Funding rate payments
    - Limit order fill simulation
    """

    def __init__(self, config: CostConfig):
        """
        Initialize cost model.

        Args:
            config: Cost configuration
        """
        self.config = config

    def calculate_fee(
        self,
        trade_value: float,
        is_maker: bool = False,
    ) -> float:
        """
        Calculate trading fee.

        Args:
            trade_value: Total trade value in quote currency
            is_maker: Whether this is a maker order (limit order)

        Returns:
            Fee amount in quote currency
        """
        fee_rate = self.config.maker_fee if is_maker else self.config.taker_fee
        return trade_value * fee_rate

    def apply_slippage(
        self,
        price: float,
        is_buy: bool,
        atr: Optional[float] = None,
        atr_multiplier: Optional[float] = None,
    ) -> float:
        """
        Apply slippage to price.

        Args:
            price: Original price
            is_buy: Whether this is a buy order
            atr: Average True Range (for dynamic slippage)
            atr_multiplier: Multiplier for ATR-based slippage

        Returns:
            Adjusted price after slippage
        """
        # Fixed percentage slippage
        fixed_slippage = price * self.config.slippage_pct

        # ATR-based slippage
        atr_slippage = 0.0
        if atr is not None and atr_multiplier is not None:
            atr_slippage = atr * atr_multiplier

        total_slippage = fixed_slippage + atr_slippage

        # Buy orders pay more, sell orders receive less
        if is_buy:
            return price + total_slippage
        else:
            return price - total_slippage

    def calculate_funding_payment(
        self,
        position_value: float,
        is_long: bool,
        funding_rate: float,
    ) -> float:
        """
        Calculate funding rate payment.

        Args:
            position_value: Position value in quote currency
            is_long: Whether position is long
            funding_rate: Funding rate (e.g., 0.0001 for 0.01%)

        Returns:
            Funding payment (positive = receive, negative = pay)
        """
        if not self.config.use_funding_rate:
            return 0.0

        if position_value == 0.0:
            return 0.0

        # Funding payment from perspective of position holder
        # Long pays when funding rate is positive, short pays when negative
        payment = position_value * funding_rate

        if is_long:
            return -payment  # Long pays positive, receives negative
        else:
            return payment  # Short receives positive, pays negative

    def check_limit_fill(
        self,
        limit_price: float,
        is_buy: bool,
        candle_low: float,
        candle_high: float,
    ) -> bool:
        """
        Check if a limit order would fill based on candle range.

        Simple model: fills if price crosses the limit price.

        Args:
            limit_price: Limit order price
            is_buy: Whether this is a buy limit order
            candle_low: Candle low price
            candle_high: Candle high price

        Returns:
            True if order would fill
        """
        if is_buy:
            # Buy limit fills when price drops to or below limit
            return candle_low <= limit_price
        else:
            # Sell limit fills when price rises to or above limit
            return candle_high >= limit_price

    def calculate_total_cost(
        self,
        price: float,
        amount: float,
        is_buy: bool,
        is_maker: bool = False,
        atr: Optional[float] = None,
        atr_multiplier: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Calculate total cost for a trade.

        Args:
            price: Entry price
            amount: Trade amount in base currency
            is_buy: Whether this is a buy order
            is_maker: Whether this is a maker order
            atr: ATR for dynamic slippage
            atr_multiplier: ATR slippage multiplier

        Returns:
            Dictionary with:
                - adjusted_price: Price after slippage
                - trade_value: Total trade value
                - fee: Trading fee
                - total_cost: Total cost (value + fee)
        """
        # Apply slippage
        adjusted_price = self.apply_slippage(
            price=price,
            is_buy=is_buy,
            atr=atr,
            atr_multiplier=atr_multiplier,
        )

        # Calculate trade value
        trade_value = adjusted_price * amount

        # Calculate fee
        fee = self.calculate_fee(
            trade_value=trade_value,
            is_maker=is_maker,
        )

        return {
            "adjusted_price": adjusted_price,
            "trade_value": trade_value,
            "fee": fee,
            "total_cost": trade_value + fee,
        }

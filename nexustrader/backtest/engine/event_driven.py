"""
Event-driven backtest engine.

Full-featured backtest implementation for complex strategy validation.
Supports limit orders, position management, and detailed trade tracking.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest.engine.cost_model import CostConfig, CostModel
from nexustrader.backtest.result import BacktestConfig, BacktestResult, TradeRecord


class OrderSide(Enum):
    """Order side."""

    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    """Order type."""

    MARKET = "market"
    LIMIT = "limit"


@dataclass
class Order:
    """Order representation."""

    timestamp: datetime
    side: OrderSide
    order_type: OrderType
    amount: float
    price: Optional[float] = None  # Required for limit orders

    def __post_init__(self):
        if self.order_type == OrderType.LIMIT and self.price is None:
            raise ValueError("Limit orders require a price")


@dataclass
class Position:
    """Position representation."""

    size: float  # Positive = long, negative = short
    entry_price: float

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.size > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.size < 0

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL."""
        return self.size * (current_price - self.entry_price)


class BaseStrategy(ABC):
    """Base class for event-driven strategies."""

    def __init__(self):
        """Initialize strategy."""
        pass

    @abstractmethod
    def on_bar(self, bar: pd.Series, position: Position) -> Optional[Order]:
        """
        Process a bar and return order if any.

        Args:
            bar: OHLCV bar data with timestamp as name
            position: Current position

        Returns:
            Order to execute, or None
        """
        pass

    def on_trade_executed(self, trade: TradeRecord) -> None:
        """
        Called when a trade is executed.

        Override to track trade history or update strategy state.

        Args:
            trade: Executed trade record
        """
        pass


class EventDrivenBacktest:
    """
    Event-driven backtest engine for complex strategy validation.

    Processes data bar-by-bar, supporting:
    - Market and limit orders
    - Position tracking
    - Detailed cost modeling
    """

    def __init__(
        self,
        config: BacktestConfig,
        strategy: BaseStrategy,
        cost_config: Optional[CostConfig] = None,
        position_size_pct: float = 1.0,
    ):
        """
        Initialize event-driven backtest.

        Args:
            config: Backtest configuration
            strategy: Trading strategy instance
            cost_config: Trading cost configuration
            position_size_pct: Fraction of capital to use per trade (default 100%)
        """
        self.config = config
        self.strategy = strategy
        self.cost_config = cost_config or CostConfig()
        self.cost_model = CostModel(self.cost_config)
        self.position_size_pct = position_size_pct

    def run(
        self,
        data: pd.DataFrame,
        funding_rates: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """
        Run event-driven backtest.

        Args:
            data: OHLCV DataFrame with DatetimeIndex
            funding_rates: Optional DataFrame with funding rate data

        Returns:
            BacktestResult with equity curve, trades, and metrics
        """
        start_time = time.time()

        n = len(data)

        # State tracking
        capital = self.config.initial_capital
        position = Position(size=0.0, entry_price=0.0)

        # Pending orders (for limit orders)
        pending_orders: List[Order] = []

        # Output arrays
        equity = np.zeros(n)
        trades: List[TradeRecord] = []

        for i in range(n):
            bar = data.iloc[i]
            price = bar["close"]
            low = bar["low"]
            high = bar["high"]
            timestamp = data.index[i]

            # Check pending limit orders
            for order in pending_orders[:]:  # Copy list for safe removal
                filled, fill_price = self._check_limit_fill(order, low, high)
                if filled:
                    capital, position, trade = self._execute_order(
                        order=order,
                        fill_price=fill_price,
                        timestamp=timestamp,
                        capital=capital,
                        position=position,
                        is_maker=True,
                    )
                    if trade:
                        trades.append(trade)
                        self.strategy.on_trade_executed(trade)
                    pending_orders.remove(order)

            # Calculate current equity before strategy
            equity[i] = self._calculate_equity(capital, position, price)

            # Get order from strategy
            order = self.strategy.on_bar(bar, position)

            if order is not None:
                if order.order_type == OrderType.MARKET:
                    # Execute market order immediately
                    capital, position, trade = self._execute_order(
                        order=order,
                        fill_price=price,
                        timestamp=timestamp,
                        capital=capital,
                        position=position,
                        is_maker=False,
                    )
                    if trade:
                        trades.append(trade)
                        self.strategy.on_trade_executed(trade)
                else:
                    # Add limit order to pending
                    pending_orders.append(order)

            # Update equity after trades
            equity[i] = self._calculate_equity(capital, position, price)

        # Create equity curve
        equity_curve = pd.Series(equity, index=data.index)

        # Calculate metrics
        metrics = self._calculate_metrics(equity_curve, trades)

        duration = time.time() - start_time

        return BacktestResult(
            config=self.config,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            run_time=datetime.now(),
            duration_seconds=duration,
        )

    def _calculate_equity(
        self,
        capital: float,
        position: Position,
        price: float,
    ) -> float:
        """Calculate current equity."""
        if position.size != 0:
            unrealized_pnl = position.unrealized_pnl(price)
            return capital + abs(position.size) * position.entry_price + unrealized_pnl
        return capital

    def _check_limit_fill(
        self,
        order: Order,
        low: float,
        high: float,
    ) -> tuple:
        """
        Check if limit order would fill.

        Returns:
            (filled: bool, fill_price: float)
        """
        if order.order_type != OrderType.LIMIT:
            return False, 0.0

        fills = self.cost_model.check_limit_fill(
            limit_price=order.price,
            is_buy=order.side == OrderSide.BUY,
            candle_low=low,
            candle_high=high,
        )

        if fills:
            return True, order.price
        return False, 0.0

    def _execute_order(
        self,
        order: Order,
        fill_price: float,
        timestamp: datetime,
        capital: float,
        position: Position,
        is_maker: bool,
    ) -> tuple:
        """
        Execute an order.

        Returns:
            (new_capital, new_position, trade_record)
        """
        is_buy = order.side == OrderSide.BUY

        # Determine if opening or closing position
        if position.size == 0:
            # Opening new position
            return self._open_position(
                timestamp=timestamp,
                price=fill_price,
                capital=capital,
                is_long=is_buy,
                is_maker=is_maker,
            )
        elif (position.is_long and not is_buy) or (position.is_short and is_buy):
            # Closing position
            new_capital, trade = self._close_position(
                timestamp=timestamp,
                price=fill_price,
                position=position,
                capital=capital,
                is_buy=is_buy,
                is_maker=is_maker,
            )
            new_position = Position(size=0.0, entry_price=0.0)
            return new_capital, new_position, trade
        else:
            # Adding to position (same direction)
            return self._add_to_position(
                timestamp=timestamp,
                price=fill_price,
                capital=capital,
                position=position,
                amount=order.amount,
                is_maker=is_maker,
            )

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        capital: float,
        is_long: bool,
        is_maker: bool,
    ) -> tuple:
        """
        Open a new position.

        Returns:
            (new_capital, new_position, trade_record)
        """
        # Calculate position size
        position_value = capital * self.position_size_pct

        # Apply costs
        cost_result = self.cost_model.calculate_total_cost(
            price=price,
            amount=position_value / price,
            is_buy=is_long,
            is_maker=is_maker,
        )

        adjusted_price = cost_result["adjusted_price"]
        fee = cost_result["fee"]

        # Calculate position
        size = position_value / adjusted_price
        if not is_long:
            size = -size

        # Update capital
        new_capital = capital - fee

        # Create position
        new_position = Position(size=size, entry_price=adjusted_price)

        # Create trade record
        trade = TradeRecord(
            timestamp=timestamp,
            side="buy" if is_long else "sell",
            price=adjusted_price,
            amount=abs(size),
            fee=fee,
            pnl=0.0,
            pnl_pct=0.0,
            position_after=size,
            capital_after=new_capital,
            entry_price=adjusted_price,
            exit_reason="",
        )

        return new_capital, new_position, trade

    def _close_position(
        self,
        timestamp: datetime,
        price: float,
        position: Position,
        capital: float,
        is_buy: bool,
        is_maker: bool,
    ) -> tuple:
        """
        Close an existing position.

        Returns:
            (new_capital, trade_record)
        """
        # Apply costs
        cost_result = self.cost_model.calculate_total_cost(
            price=price,
            amount=abs(position.size),
            is_buy=is_buy,
            is_maker=is_maker,
        )

        adjusted_price = cost_result["adjusted_price"]
        fee = cost_result["fee"]

        # Calculate PnL
        if position.is_long:
            pnl = position.size * (adjusted_price - position.entry_price) - fee
        else:
            pnl = -position.size * (position.entry_price - adjusted_price) - fee

        pnl_pct = (pnl / (abs(position.size) * position.entry_price)) * 100 if position.entry_price > 0 else 0.0

        # Update capital
        position_value = abs(position.size) * position.entry_price
        new_capital = capital + position_value + pnl

        # Create trade record
        trade = TradeRecord(
            timestamp=timestamp,
            side="buy" if is_buy else "sell",
            price=adjusted_price,
            amount=abs(position.size),
            fee=fee,
            pnl=pnl,
            pnl_pct=pnl_pct,
            position_after=0.0,
            capital_after=new_capital,
            entry_price=position.entry_price,
            exit_reason="signal",
        )

        return new_capital, trade

    def _add_to_position(
        self,
        timestamp: datetime,
        price: float,
        capital: float,
        position: Position,
        amount: float,
        is_maker: bool,
    ) -> tuple:
        """
        Add to existing position.

        Returns:
            (new_capital, new_position, trade_record)
        """
        is_long = position.is_long

        # Apply costs
        cost_result = self.cost_model.calculate_total_cost(
            price=price,
            amount=amount,
            is_buy=is_long,
            is_maker=is_maker,
        )

        adjusted_price = cost_result["adjusted_price"]
        fee = cost_result["fee"]

        # Calculate new position
        new_size = position.size + (amount if is_long else -amount)
        # Weighted average entry price
        old_value = abs(position.size) * position.entry_price
        new_value = amount * adjusted_price
        new_entry_price = (old_value + new_value) / abs(new_size) if new_size != 0 else 0.0

        # Update capital
        new_capital = capital - fee

        # Create new position
        new_position = Position(size=new_size, entry_price=new_entry_price)

        # Create trade record
        trade = TradeRecord(
            timestamp=timestamp,
            side="buy" if is_long else "sell",
            price=adjusted_price,
            amount=amount,
            fee=fee,
            pnl=0.0,
            pnl_pct=0.0,
            position_after=new_size,
            capital_after=new_capital,
            entry_price=new_entry_price,
            exit_reason="",
        )

        return new_capital, new_position, trade

    def _calculate_metrics(
        self,
        equity_curve: pd.Series,
        trades: List[TradeRecord],
    ) -> Dict[str, float]:
        """Calculate performance metrics."""
        initial = self.config.initial_capital
        final = equity_curve.iloc[-1]

        # Total return
        total_return = (final - initial) / initial
        total_return_pct = total_return * 100

        # Drawdown
        rolling_max = equity_curve.cummax()
        drawdown = (equity_curve - rolling_max) / rolling_max
        max_drawdown_pct = abs(drawdown.min()) * 100

        # Trade statistics
        closing_trades = [t for t in trades if t.pnl != 0]
        total_trades = len(closing_trades)
        winning_trades = [t for t in closing_trades if t.pnl > 0]
        win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0

        # Sharpe ratio (simplified, assuming 15-min bars)
        returns = equity_curve.pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            # Annualize: 4 bars/hour * 24 hours * 365 days
            sharpe = returns.mean() / returns.std() * np.sqrt(4 * 24 * 365)
        else:
            sharpe = 0.0

        return {
            "total_return_pct": total_return_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "total_trades": total_trades,
            "win_rate_pct": win_rate,
            "sharpe_ratio": sharpe,
            "final_equity": final,
        }

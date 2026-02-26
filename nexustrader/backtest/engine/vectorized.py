"""
Vectorized backtest engine.

Fast backtest implementation using numpy/pandas for parameter optimization.
"""

import time
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

from nexustrader.backtest.engine.cost_model import CostConfig, CostModel
from nexustrader.backtest.result import BacktestConfig, BacktestResult, TradeRecord


class Signal(Enum):
    """Trading signal types."""

    HOLD = 0
    BUY = 1
    SELL = -1
    CLOSE = 2


class VectorizedBacktest:
    """
    Vectorized backtest engine for fast parameter optimization.

    Uses numpy arrays for efficient signal processing and position tracking.
    """

    def __init__(
        self,
        config: BacktestConfig,
        cost_config: Optional[CostConfig] = None,
        position_size_pct: float = 1.0,
    ):
        """
        Initialize vectorized backtest.

        Args:
            config: Backtest configuration
            cost_config: Trading cost configuration
            position_size_pct: Fraction of capital to use per trade (default 100%)
        """
        self.config = config
        self.cost_config = cost_config or CostConfig()
        self.cost_model = CostModel(self.cost_config)
        self.position_size_pct = position_size_pct

    def run(
        self,
        data: pd.DataFrame,
        signals: Union[np.ndarray, pd.Series],
        funding_rates: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """
        Run vectorized backtest.

        Args:
            data: OHLCV DataFrame with DatetimeIndex
            signals: Array of Signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE)
            funding_rates: Optional DataFrame with 'funding_rate' column and DatetimeIndex

        Returns:
            BacktestResult with equity curve, trades, and metrics
        """
        start_time = time.time()

        # Convert signals to numpy array
        if isinstance(signals, pd.Series):
            signals = signals.values

        n = len(data)
        prices = data["close"].values

        # State tracking
        capital = self.config.initial_capital
        position = 0.0  # Position in base currency (positive = long, negative = short)
        entry_price = 0.0
        total_funding_paid = 0.0

        # Prepare funding rate lookup
        # Funding rates settle at 00:00, 08:00, 16:00 UTC
        # We create a map keyed by (date, hour) for efficient lookup
        funding_rate_map = {}
        if funding_rates is not None and not funding_rates.empty and self.cost_config.use_funding_rate:
            for ts, row in funding_rates.iterrows():
                # Normalize to (date, hour) key for matching with OHLCV bars
                if hasattr(ts, 'date'):
                    key = (ts.date(), ts.hour)
                    funding_rate_map[key] = row.get("funding_rate", 0.0)

        # Output arrays
        equity = np.zeros(n)
        trades: List[TradeRecord] = []

        for i in range(n):
            price = prices[i]
            signal = signals[i]

            # Calculate current equity (capital + margin + unrealized PnL)
            if position != 0:
                unrealized_pnl = position * (price - entry_price)
                margin = abs(position) * entry_price / self.config.leverage
                equity[i] = capital + margin + unrealized_pnl
            else:
                equity[i] = capital

            # Process signal
            if signal == Signal.BUY.value and position <= 0:
                # Close short if exists
                if position < 0:
                    capital, trade = self._close_position(
                        timestamp=data.index[i],
                        price=price,
                        position=position,
                        entry_price=entry_price,
                        capital=capital,
                        is_buy=True,
                    )
                    trades.append(trade)

                # Open long
                capital, position, entry_price, trade = self._open_position(
                    timestamp=data.index[i],
                    price=price,
                    capital=capital,
                    is_long=True,
                )
                trades.append(trade)

            elif signal == Signal.SELL.value and position >= 0:
                # Close long if exists
                if position > 0:
                    capital, trade = self._close_position(
                        timestamp=data.index[i],
                        price=price,
                        position=position,
                        entry_price=entry_price,
                        capital=capital,
                        is_buy=False,
                    )
                    trades.append(trade)

                # Open short
                capital, position, entry_price, trade = self._open_position(
                    timestamp=data.index[i],
                    price=price,
                    capital=capital,
                    is_long=False,
                )
                trades.append(trade)

            elif signal == Signal.CLOSE.value and position != 0:
                # Close position
                is_buy = position < 0  # Buy to close short
                capital, trade = self._close_position(
                    timestamp=data.index[i],
                    price=price,
                    position=position,
                    entry_price=entry_price,
                    capital=capital,
                    is_buy=is_buy,
                )
                trades.append(trade)
                position = 0.0
                entry_price = 0.0

            # Apply funding rate if holding a position
            # Funding settles at 00:00, 08:00, 16:00 UTC
            if position != 0 and funding_rate_map:
                current_ts = data.index[i]
                # Check if this bar is at a funding settlement time (minute=0, hour in [0, 8, 16])
                if hasattr(current_ts, 'minute') and current_ts.minute == 0 and current_ts.hour in (0, 8, 16):
                    key = (current_ts.date(), current_ts.hour)
                    if key in funding_rate_map:
                        funding_rate = funding_rate_map[key]
                        position_value = abs(position) * price
                        funding_payment = self.cost_model.calculate_funding_payment(
                            position_value=position_value,
                            is_long=(position > 0),
                            funding_rate=funding_rate,
                        )
                        capital += funding_payment
                        total_funding_paid -= funding_payment  # Track as cost (negative = paid)

            # Update equity after trades and funding
            if position != 0:
                unrealized_pnl = position * (price - entry_price)
                margin = abs(position) * entry_price / self.config.leverage
                equity[i] = capital + margin + unrealized_pnl
            else:
                equity[i] = capital

        # Create equity curve
        equity_curve = pd.Series(equity, index=data.index)

        # Calculate metrics
        metrics = self._calculate_metrics(equity_curve, trades)
        metrics["total_funding_paid"] = total_funding_paid

        duration = time.time() - start_time

        return BacktestResult(
            config=self.config,
            equity_curve=equity_curve,
            trades=trades,
            metrics=metrics,
            run_time=datetime.now(),
            duration_seconds=duration,
        )

    def _open_position(
        self,
        timestamp: datetime,
        price: float,
        capital: float,
        is_long: bool,
    ) -> tuple:
        """
        Open a new position.

        With leverage, position_value is amplified but only margin
        (position_value / leverage) is deducted from capital.

        Returns:
            (new_capital, position, entry_price, trade_record)
        """
        leverage = self.config.leverage

        # Calculate position size (leveraged)
        position_value = capital * self.position_size_pct * leverage

        # Apply costs
        cost_result = self.cost_model.calculate_total_cost(
            price=price,
            amount=position_value / price,
            is_buy=is_long,
            is_maker=False,
        )

        adjusted_price = cost_result["adjusted_price"]
        fee = cost_result["fee"]

        # Calculate position
        position = position_value / adjusted_price
        if not is_long:
            position = -position

        # Update capital: deduct margin (position_value / leverage) and fee
        margin = position_value / leverage
        new_capital = capital - margin - fee

        # Create trade record
        trade = TradeRecord(
            timestamp=timestamp,
            side="buy" if is_long else "sell",
            price=adjusted_price,
            amount=abs(position),
            fee=fee,
            pnl=0.0,
            pnl_pct=0.0,
            position_after=position,
            capital_after=new_capital,
            entry_price=adjusted_price,
            exit_reason="",
        )

        return new_capital, position, adjusted_price, trade

    def _close_position(
        self,
        timestamp: datetime,
        price: float,
        position: float,
        entry_price: float,
        capital: float,
        is_buy: bool,
    ) -> tuple:
        """
        Close an existing position.

        Returns margin back to capital plus realized PnL.

        Returns:
            (new_capital, trade_record)
        """
        leverage = self.config.leverage

        # Apply costs
        cost_result = self.cost_model.calculate_total_cost(
            price=price,
            amount=abs(position),
            is_buy=is_buy,
            is_maker=False,
        )

        adjusted_price = cost_result["adjusted_price"]
        fee = cost_result["fee"]

        # Calculate PnL (on full leveraged position)
        if position > 0:  # Long position
            pnl = position * (adjusted_price - entry_price) - fee
        else:  # Short position
            pnl = -position * (entry_price - adjusted_price) - fee

        # PnL percentage relative to margin (not full position value)
        margin = abs(position) * entry_price / leverage
        pnl_pct = (pnl / margin) * 100 if margin > 0 else 0.0

        # Update capital: return margin + PnL
        new_capital = capital + margin + pnl

        # Create trade record
        trade = TradeRecord(
            timestamp=timestamp,
            side="buy" if is_buy else "sell",
            price=adjusted_price,
            amount=abs(position),
            fee=fee,
            pnl=pnl,
            pnl_pct=pnl_pct,
            position_after=0.0,
            capital_after=new_capital,
            entry_price=entry_price,
            exit_reason="signal",
        )

        return new_capital, trade

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

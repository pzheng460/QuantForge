"""
Backtest result data classes.

Contains configuration, trade records, and result structures.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from quantforge.constants import KlineInterval


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""

    symbol: str
    interval: KlineInterval
    start_date: datetime
    end_date: datetime
    initial_capital: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage_pct: float = 0.0005
    use_funding_rate: bool = True
    exchange: str = ""
    leverage: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "symbol": self.symbol,
            "interval": self.interval.value if isinstance(self.interval, KlineInterval) else self.interval,
            "start_date": self.start_date.isoformat() if isinstance(self.start_date, datetime) else self.start_date,
            "end_date": self.end_date.isoformat() if isinstance(self.end_date, datetime) else self.end_date,
            "initial_capital": self.initial_capital,
            "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee,
            "slippage_pct": self.slippage_pct,
            "use_funding_rate": self.use_funding_rate,
            "exchange": self.exchange,
            "leverage": self.leverage,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestConfig":
        """Create config from dictionary."""
        # Handle interval conversion
        interval = data.get("interval")
        if isinstance(interval, str):
            interval = KlineInterval(interval)

        # Handle datetime conversion
        start_date = data.get("start_date")
        if isinstance(start_date, str):
            start_date = datetime.fromisoformat(start_date)

        end_date = data.get("end_date")
        if isinstance(end_date, str):
            end_date = datetime.fromisoformat(end_date)

        return cls(
            symbol=data["symbol"],
            interval=interval,
            start_date=start_date,
            end_date=end_date,
            initial_capital=data.get("initial_capital", 10000.0),
            maker_fee=data.get("maker_fee", 0.0002),
            taker_fee=data.get("taker_fee", 0.0005),
            slippage_pct=data.get("slippage_pct", 0.0005),
            use_funding_rate=data.get("use_funding_rate", True),
            exchange=data.get("exchange", ""),
            leverage=data.get("leverage", 1.0),
        )


@dataclass
class TradeRecord:
    """Record of a single trade."""

    timestamp: datetime
    side: str  # "buy" or "sell"
    price: float
    amount: float
    fee: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    position_after: float = 0.0
    capital_after: float = 0.0
    entry_price: Optional[float] = None
    exit_reason: str = ""  # "signal", "stop_loss", "take_profit"
    entry_time: Optional[datetime] = None  # Bar timestamp when position was opened
    bars_held: int = 0  # Number of bars the position was held

    def to_dict(self) -> Dict[str, Any]:
        """Convert trade to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else self.timestamp,
            "side": self.side,
            "price": self.price,
            "amount": self.amount,
            "fee": self.fee,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "position_after": self.position_after,
            "capital_after": self.capital_after,
            "entry_price": self.entry_price,
            "exit_reason": self.exit_reason,
            "entry_time": self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else self.entry_time,
            "bars_held": self.bars_held,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeRecord":
        """Create trade from dictionary."""
        timestamp = data.get("timestamp")
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp)

        entry_time = data.get("entry_time")
        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)

        return cls(
            timestamp=timestamp,
            side=data["side"],
            price=data["price"],
            amount=data["amount"],
            fee=data["fee"],
            pnl=data.get("pnl", 0.0),
            pnl_pct=data.get("pnl_pct", 0.0),
            position_after=data.get("position_after", 0.0),
            capital_after=data.get("capital_after", 0.0),
            entry_price=data.get("entry_price"),
            exit_reason=data.get("exit_reason", ""),
            entry_time=entry_time,
            bars_held=data.get("bars_held", 0),
        )


@dataclass
class BacktestResult:
    """Complete backtest result."""

    config: BacktestConfig
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trades: List[TradeRecord] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    run_time: datetime = field(default_factory=datetime.now)
    duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "config": self.config.to_dict(),
            "equity_curve": {
                "index": [ts.isoformat() if hasattr(ts, "isoformat") else str(ts) for ts in self.equity_curve.index.tolist()],
                "values": self.equity_curve.tolist(),
            },
            "trades": [t.to_dict() for t in self.trades],
            "metrics": self.metrics,
            "run_time": self.run_time.isoformat() if isinstance(self.run_time, datetime) else self.run_time,
            "duration_seconds": self.duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BacktestResult":
        """Create result from dictionary."""
        # Reconstruct config
        config = BacktestConfig.from_dict(data["config"])

        # Reconstruct equity curve
        eq_data = data.get("equity_curve", {})
        if eq_data:
            index = pd.to_datetime(eq_data.get("index", []))
            values = eq_data.get("values", [])
            equity_curve = pd.Series(values, index=index)
        else:
            equity_curve = pd.Series(dtype=float)

        # Reconstruct trades
        trades = [TradeRecord.from_dict(t) for t in data.get("trades", [])]

        # Reconstruct run_time
        run_time = data.get("run_time")
        if isinstance(run_time, str):
            run_time = datetime.fromisoformat(run_time)
        elif run_time is None:
            run_time = datetime.now()

        return cls(
            config=config,
            equity_curve=equity_curve,
            trades=trades,
            metrics=data.get("metrics", {}),
            run_time=run_time,
            duration_seconds=data.get("duration_seconds", 0.0),
        )

    def save(self, file_path: Union[str, Path]) -> None:
        """
        Save result to JSON file.

        Args:
            file_path: Path to save the result
        """
        file_path = Path(file_path)
        with open(file_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, file_path: Union[str, Path]) -> "BacktestResult":
        """
        Load result from JSON file.

        Args:
            file_path: Path to the saved result

        Returns:
            BacktestResult instance
        """
        file_path = Path(file_path)
        with open(file_path, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"BacktestResult("
            f"symbol={self.config.symbol}, "
            f"trades={len(self.trades)}, "
            f"return={self.metrics.get('total_return_pct', 0):.2f}%)"
        )

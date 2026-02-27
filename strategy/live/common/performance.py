"""
Performance Tracker for Live/Demo Trading.

Tracks and persists trading performance metrics including:
- Total return
- Win rate
- Max drawdown
- Trade history

Usage:
    tracker = PerformanceTracker(initial_balance=10000)
    tracker.record_trade(entry_price=100, exit_price=105, side="long", amount=1)
    tracker.print_stats()
    tracker.save()
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class TradeRecord:
    """Record of a single completed trade."""

    symbol: str
    side: str  # "long" or "short"
    entry_price: float
    exit_price: float
    amount: float
    entry_time: str
    exit_time: str
    pnl: float  # Profit/Loss in USDT
    pnl_pct: float  # Profit/Loss percentage
    exit_reason: str = ""  # "signal", "stop_loss", "take_profit"


@dataclass
class PerformanceStats:
    """Aggregated performance statistics."""

    # Session info
    start_time: str = ""
    last_update: str = ""
    mesa_index: int = 0
    config_name: str = ""

    # Balance tracking
    initial_balance: float = 0.0
    current_balance: float = 0.0
    peak_balance: float = 0.0

    # Performance metrics
    total_return_pct: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate_pct: float = 0.0

    # Average metrics
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    profit_factor: float = 0.0

    # Trade history
    trades: List[dict] = field(default_factory=list)


class PerformanceTracker:
    """Tracks and persists trading performance."""

    def __init__(
        self,
        initial_balance: float = 0.0,
        mesa_index: int = 0,
        config_name: str = "",
        stats_file: Optional[Path] = None,
    ):
        self._stats_file = stats_file or Path(__file__).parent / "live_performance.json"

        # Try to load existing stats
        self._stats = self._load_stats()

        # If new session or balance changed significantly, reset
        if initial_balance > 0:
            if (
                self._stats.initial_balance == 0
                or abs(self._stats.initial_balance - initial_balance)
                > initial_balance * 0.5
            ):
                # New session - reset stats
                self._stats = PerformanceStats(
                    start_time=datetime.now().isoformat(),
                    initial_balance=initial_balance,
                    current_balance=initial_balance,
                    peak_balance=initial_balance,
                    mesa_index=mesa_index,
                    config_name=config_name,
                )
            else:
                # Continuing session - update current balance
                self._stats.current_balance = initial_balance
                self._stats.mesa_index = mesa_index
                self._stats.config_name = config_name

        self._stats.last_update = datetime.now().isoformat()

        # Track current open position for PnL calculation
        self._open_position: Optional[dict] = None

    def _load_stats(self) -> PerformanceStats:
        """Load stats from file if exists."""
        if self._stats_file.exists():
            try:
                with open(self._stats_file, "r") as f:
                    data = json.load(f)
                    trades = data.pop("trades", [])
                    stats = PerformanceStats(**data)
                    stats.trades = trades
                    return stats
            except (json.JSONDecodeError, TypeError, KeyError):
                pass
        return PerformanceStats()

    def save(self) -> None:
        """Save stats to file."""
        self._stats.last_update = datetime.now().isoformat()
        with open(self._stats_file, "w") as f:
            json.dump(asdict(self._stats), f, indent=2)

    def update_balance(self, balance: float) -> None:
        """Update current balance and recalculate metrics."""
        self._stats.current_balance = balance

        # Update peak
        if balance > self._stats.peak_balance:
            self._stats.peak_balance = balance

        # Calculate return
        if self._stats.initial_balance > 0:
            self._stats.total_return_pct = (
                (balance - self._stats.initial_balance) / self._stats.initial_balance
            ) * 100
            self._stats.total_pnl = balance - self._stats.initial_balance

        # Calculate drawdown
        if self._stats.peak_balance > 0:
            self._stats.current_drawdown_pct = (
                (self._stats.peak_balance - balance) / self._stats.peak_balance
            ) * 100
            if self._stats.current_drawdown_pct > self._stats.max_drawdown_pct:
                self._stats.max_drawdown_pct = self._stats.current_drawdown_pct

        self._stats.last_update = datetime.now().isoformat()

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        amount: float,
    ) -> None:
        """Record opening a position."""
        self._open_position = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "amount": amount,
            "entry_time": datetime.now().isoformat(),
        }

    def close_position(
        self,
        exit_price: float,
        exit_reason: str = "signal",
    ) -> Optional[TradeRecord]:
        """Record closing a position and calculate PnL."""
        if not self._open_position:
            return None

        pos = self._open_position

        # Calculate PnL
        if pos["side"] == "long":
            pnl = (exit_price - pos["entry_price"]) * pos["amount"]
            pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
        else:  # short
            pnl = (pos["entry_price"] - exit_price) * pos["amount"]
            pnl_pct = ((pos["entry_price"] - exit_price) / pos["entry_price"]) * 100

        trade = TradeRecord(
            symbol=pos["symbol"],
            side=pos["side"],
            entry_price=pos["entry_price"],
            exit_price=exit_price,
            amount=pos["amount"],
            entry_time=pos["entry_time"],
            exit_time=datetime.now().isoformat(),
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
        )

        # Update stats
        self._stats.trades.append(asdict(trade))
        self._stats.total_trades += 1

        if pnl > 0:
            self._stats.winning_trades += 1
        else:
            self._stats.losing_trades += 1

        # Update win rate
        if self._stats.total_trades > 0:
            self._stats.win_rate_pct = (
                self._stats.winning_trades / self._stats.total_trades
            ) * 100

        # Update average win/loss
        wins = [t["pnl_pct"] for t in self._stats.trades if t["pnl"] > 0]
        losses = [t["pnl_pct"] for t in self._stats.trades if t["pnl"] <= 0]

        if wins:
            self._stats.avg_win_pct = sum(wins) / len(wins)
        if losses:
            self._stats.avg_loss_pct = sum(losses) / len(losses)

        # Calculate profit factor
        total_wins = sum(t["pnl"] for t in self._stats.trades if t["pnl"] > 0)
        total_losses = abs(sum(t["pnl"] for t in self._stats.trades if t["pnl"] <= 0))
        if total_losses > 0:
            self._stats.profit_factor = total_wins / total_losses

        self._open_position = None
        self.save()

        return trade

    def record_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        amount: float,
        exit_reason: str = "signal",
    ) -> TradeRecord:
        """Record a complete trade (entry + exit)."""
        self.open_position(symbol, side, entry_price, amount)
        return self.close_position(exit_price, exit_reason)

    def get_stats(self) -> PerformanceStats:
        """Get current statistics."""
        return self._stats

    def get_stats_summary(self) -> str:
        """Get a formatted summary of performance stats."""
        s = self._stats
        lines = [
            "=" * 60,
            "LIVE PERFORMANCE STATS",
            "=" * 60,
            f"Session Start: {s.start_time[:19] if s.start_time else 'N/A'}",
            f"Config: Mesa #{s.mesa_index} ({s.config_name})",
            "-" * 60,
            f"Initial Balance:  {s.initial_balance:,.2f} USDT",
            f"Current Balance:  {s.current_balance:,.2f} USDT",
            f"Total P&L:        {s.total_pnl:+,.2f} USDT ({s.total_return_pct:+.2f}%)",
            "-" * 60,
            f"Total Trades:     {s.total_trades}",
            f"Win Rate:         {s.win_rate_pct:.1f}% ({s.winning_trades}W / {s.losing_trades}L)",
            f"Avg Win:          {s.avg_win_pct:+.2f}%",
            f"Avg Loss:         {s.avg_loss_pct:+.2f}%",
            f"Profit Factor:    {s.profit_factor:.2f}",
            "-" * 60,
            f"Max Drawdown:     {s.max_drawdown_pct:.2f}%",
            f"Current Drawdown: {s.current_drawdown_pct:.2f}%",
            "=" * 60,
        ]
        return "\n".join(lines)

    def print_stats(self) -> None:
        """Print performance stats to console."""
        print(self.get_stats_summary())

    def reset(self, initial_balance: float = 0.0) -> None:
        """Reset all stats for a new session."""
        self._stats = PerformanceStats(
            start_time=datetime.now().isoformat(),
            initial_balance=initial_balance,
            current_balance=initial_balance,
            peak_balance=initial_balance,
            mesa_index=self._stats.mesa_index,
            config_name=self._stats.config_name,
        )
        self._open_position = None
        self.save()


def print_live_performance(stats_file: Optional[Path] = None) -> None:
    """Print current live performance stats."""
    tracker = PerformanceTracker(stats_file=stats_file)
    tracker.print_stats()

    # Also print recent trades
    trades = tracker.get_stats().trades
    if trades:
        print("\nRECENT TRADES (last 10):")
        print("-" * 60)
        for trade in trades[-10:]:
            print(
                f"  {trade['exit_time'][:19]} | {trade['side']:5} | "
                f"Entry: {trade['entry_price']:.2f} -> Exit: {trade['exit_price']:.2f} | "
                f"P&L: {trade['pnl']:+.2f} ({trade['pnl_pct']:+.2f}%)"
            )


if __name__ == "__main__":
    print_live_performance()

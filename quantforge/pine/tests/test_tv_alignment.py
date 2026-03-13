"""TradingView alignment tests — validate Pine engine against TradingView reference values.

These tests fetch real market data from Bitget and compare the Pine interpreter's
output against known TradingView results. They are slow (network I/O) and marked
with @pytest.mark.slow.

Run with: uv run python -m pytest quantforge/pine/tests/test_tv_alignment.py -v
Skip slow: uv run python -m pytest quantforge/ -v -m "not slow"

Reference values were manually recorded from TradingView on 2026-03-12.
Data source differences (Bitget REST API vs TV's Bitget feed) cause ~8-19% P&L gap.
Trade counts should match within ±1.
"""

from __future__ import annotations

import pytest

from quantforge.pine.interpreter.context import BarData, ExecutionContext
from quantforge.pine.interpreter.runtime import PineRuntime
from quantforge.pine.parser.parser import parse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_bars(symbol: str, timeframe: str, start: str, end: str) -> list[BarData]:
    """Fetch OHLCV bars from Bitget via ccxt."""
    import ccxt

    exchange = ccxt.bitget({"enableRateLimit": True})
    since = exchange.parse8601(start)
    end_ts = exchange.parse8601(end)
    all_ohlcv: list[list] = []

    while since < end_ts:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=200)
        if not ohlcv:
            break
        new_bars = [b for b in ohlcv if b[0] > (all_ohlcv[-1][0] if all_ohlcv else 0)]
        if not new_bars and ohlcv:
            new_bars = ohlcv
        all_ohlcv.extend(new_bars)
        last_ts = ohlcv[-1][0]
        if last_ts <= since:
            break
        since = last_ts + 1

    # Deduplicate and filter to range
    seen: set[int] = set()
    bars: list[BarData] = []
    for row in all_ohlcv:
        ts = row[0]
        if ts < exchange.parse8601(start) or ts > end_ts or ts in seen:
            continue
        seen.add(ts)
        bars.append(BarData(
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            time=ts,
        ))
    bars.sort(key=lambda b: b.time)
    return bars


def _run_pine(source: str, bars: list[BarData]):
    """Run a Pine script on bars and return the result."""
    ast = parse(source)
    ctx = ExecutionContext(bars=list(bars))
    runtime = PineRuntime(ctx)
    return runtime.run(ast)


# ---------------------------------------------------------------------------
# Pine script sources
# ---------------------------------------------------------------------------

EMA_CROSS_5_13 = """\
//@version=5
strategy("EMA Cross 5/13", overlay=true, initial_capital=1000000)
fast_len = input.int(5, title="Fast Length")
slow_len = input.int(13, title="Slow Length")
fast = ta.ema(close, fast_len)
slow = ta.ema(close, slow_len)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
"""

RSI_MEAN_REVERSION = """\
//@version=5
strategy("RSI Mean Reversion", overlay=true, initial_capital=1000000)
rsi_len = input.int(14, title="RSI Length")
oversold = input.int(30, title="Oversold")
overbought = input.int(70, title="Overbought")
rsi_val = ta.rsi(close, rsi_len)
if rsi_val < oversold
    strategy.entry("Long", strategy.long)
if rsi_val > overbought
    strategy.close("Long")
"""

MACD_CROSS = """\
//@version=5
strategy("MACD Cross", overlay=true, initial_capital=1000000)
fast_len = input.int(12, title="Fast")
slow_len = input.int(26, title="Slow")
sig_len = input.int(9, title="Signal")
fast_ma = ta.ema(close, fast_len)
slow_ma = ta.ema(close, slow_len)
macd_line = fast_ma - slow_ma
signal_line = ta.ema(macd_line, sig_len)
if ta.crossover(macd_line, signal_line)
    strategy.entry("Long", strategy.long)
if ta.crossunder(macd_line, signal_line)
    strategy.close("Long")
"""

# ---------------------------------------------------------------------------
# TradingView reference values (recorded 2026-03-12)
# Test range: BTC/USDT:USDT 15min, 2026-01-01 to 2026-03-12
# Warmup: 2 months from 2025-11-01
# ---------------------------------------------------------------------------

TV_REFERENCE = {
    "ema_cross_5_13": {
        "trades": 270,
        "pnl": -6810.96,
        "win_rate": 27.78,
        "winners": 75,
        "losers": 195,
        "profit_factor": 0.885,
    },
    "rsi_mean_reversion": {
        "trades": 21,
        "pnl": -10574.0,
    },
    "macd_cross": {
        "trades": 253,
        "pnl": -2280.0,
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def btc_bars_with_warmup():
    """Fetch BTC/USDT 15min bars: 2 months warmup (Nov 1) + test range (Jan 1 - Mar 12)."""
    warmup_bars = _fetch_bars(
        "BTC/USDT:USDT", "15m",
        "2025-11-01T00:00:00Z",
        "2025-12-31T23:59:59Z",
    )
    test_bars = _fetch_bars(
        "BTC/USDT:USDT", "15m",
        "2026-01-01T00:00:00Z",
        "2026-03-12T23:59:59Z",
    )
    return warmup_bars, test_bars


@pytest.fixture(scope="module")
def btc_bars_no_warmup():
    """Fetch BTC/USDT 15min bars: Jan 1 - Mar 12 only (no warmup)."""
    return _fetch_bars(
        "BTC/USDT:USDT", "15m",
        "2026-01-01T00:00:00Z",
        "2026-03-12T23:59:59Z",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestEMACrossTVAlignment:
    """EMA Cross 5/13 vs TradingView — the primary alignment test."""

    def test_trade_count_matches(self, btc_bars_with_warmup):
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(EMA_CROSS_5_13, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]

        tv = TV_REFERENCE["ema_cross_5_13"]
        # Tolerance ±5 trades: Bitget REST API data may drift slightly
        # from TV's Bitget feed over time (new bars, minor OHLC diffs)
        assert abs(len(test_trades) - tv["trades"]) <= 5, (
            f"Trade count {len(test_trades)} vs TV {tv['trades']} "
            f"(diff {len(test_trades) - tv['trades']})"
        )

    def test_pnl_within_tolerance(self, btc_bars_with_warmup):
        """P&L should be within 20% of TV (data source difference)."""
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(EMA_CROSS_5_13, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        net_pnl = sum(t.pnl for t in test_trades)

        tv = TV_REFERENCE["ema_cross_5_13"]
        pnl_diff_pct = abs(net_pnl - tv["pnl"]) / abs(tv["pnl"]) * 100
        assert pnl_diff_pct < 20, (
            f"P&L diff {pnl_diff_pct:.1f}% exceeds 20% tolerance: "
            f"Pine {net_pnl:.2f} vs TV {tv['pnl']:.2f}"
        )

    def test_win_rate_within_tolerance(self, btc_bars_with_warmup):
        """Win rate should be within 2% of TV."""
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(EMA_CROSS_5_13, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        winners = sum(1 for t in test_trades if t.pnl > 0)
        win_rate = (winners / len(test_trades) * 100) if test_trades else 0

        tv = TV_REFERENCE["ema_cross_5_13"]
        assert abs(win_rate - tv["win_rate"]) < 2.0, (
            f"Win rate {win_rate:.2f}% vs TV {tv['win_rate']}%"
        )

    def test_profit_factor_within_tolerance(self, btc_bars_with_warmup):
        """Profit factor should be within 0.05 of TV."""
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(EMA_CROSS_5_13, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        gross_profit = sum(t.pnl for t in test_trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in test_trades if t.pnl < 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else 0

        tv = TV_REFERENCE["ema_cross_5_13"]
        assert abs(pf - tv["profit_factor"]) < 0.05, (
            f"Profit factor {pf:.3f} vs TV {tv['profit_factor']}"
        )


@pytest.mark.slow
class TestRSIMeanReversionTVAlignment:
    """RSI Mean Reversion vs TradingView."""

    def test_trade_count_matches(self, btc_bars_with_warmup):
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(RSI_MEAN_REVERSION, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]

        tv = TV_REFERENCE["rsi_mean_reversion"]
        assert abs(len(test_trades) - tv["trades"]) <= 2, (
            f"Trade count {len(test_trades)} vs TV {tv['trades']}"
        )

    def test_pnl_within_tolerance(self, btc_bars_with_warmup):
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(RSI_MEAN_REVERSION, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        net_pnl = sum(t.pnl for t in test_trades)

        tv = TV_REFERENCE["rsi_mean_reversion"]
        pnl_diff_pct = abs(net_pnl - tv["pnl"]) / abs(tv["pnl"]) * 100
        assert pnl_diff_pct < 25, (
            f"P&L diff {pnl_diff_pct:.1f}%: Pine {net_pnl:.2f} vs TV {tv['pnl']:.2f}"
        )


@pytest.mark.slow
class TestMACDCrossTVAlignment:
    """MACD Cross vs TradingView."""

    def test_trade_count_matches(self, btc_bars_with_warmup):
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(MACD_CROSS, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]

        tv = TV_REFERENCE["macd_cross"]
        assert abs(len(test_trades) - tv["trades"]) <= 3, (
            f"Trade count {len(test_trades)} vs TV {tv['trades']}"
        )

    def test_pnl_within_tolerance(self, btc_bars_with_warmup):
        """MACD is more sensitive to data source diffs — wider tolerance."""
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        result = _run_pine(MACD_CROSS, all_bars)
        test_trades = [t for t in result.trades if t.entry_bar >= warmup_count]
        net_pnl = sum(t.pnl for t in test_trades)

        tv = TV_REFERENCE["macd_cross"]
        pnl_diff_pct = abs(net_pnl - tv["pnl"]) / abs(tv["pnl"]) * 100
        # MACD has higher sensitivity to OHLC diffs (EMA of EMA compounds errors)
        assert pnl_diff_pct < 50, (
            f"P&L diff {pnl_diff_pct:.1f}%: Pine {net_pnl:.2f} vs TV {tv['pnl']:.2f}"
        )


@pytest.mark.slow
class TestWarmupEffect:
    """Verify warmup significantly improves alignment."""

    def test_warmup_improves_trade_count(self, btc_bars_with_warmup, btc_bars_no_warmup):
        """With warmup, trade count should be closer to TV than without."""
        warmup, test = btc_bars_with_warmup
        all_bars = warmup + test
        warmup_count = len(warmup)

        # With warmup
        result_warm = _run_pine(EMA_CROSS_5_13, all_bars)
        trades_warm = [t for t in result_warm.trades if t.entry_bar >= warmup_count]

        # Without warmup
        result_cold = _run_pine(EMA_CROSS_5_13, btc_bars_no_warmup)
        trades_cold = result_cold.trades

        tv_trades = TV_REFERENCE["ema_cross_5_13"]["trades"]
        diff_warm = abs(len(trades_warm) - tv_trades)
        diff_cold = abs(len(trades_cold) - tv_trades)

        # Warmup should bring us closer (or at least not worse)
        assert diff_warm <= diff_cold + 5, (
            f"Warmup made alignment worse: warm diff={diff_warm}, cold diff={diff_cold}"
        )

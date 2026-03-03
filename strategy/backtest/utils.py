"""
Common backtest utilities shared across strategies.

Provides:
- Data fetching (OHLCV + funding rates via CCXT)
- Backtest/cost configuration factories
- Result persistence (JSON load/save)
- Result table printing
- Period constants and three-stage test config
- Module import helper for script execution
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from nexustrader.backtest import BacktestConfig, CostConfig
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider
from nexustrader.backtest.data.funding_rate import FundingRateProvider
from nexustrader.constants import KlineInterval


# =============================================================================
# CONSTANTS
# =============================================================================

# Period options (1 week to 5 years)
PERIODS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
    "3y": 1095,
    "5y": 1825,
}

# Periods too short for optimization / WFO — only suitable for single run
SHORT_PERIODS = {"1w", "1m", "3m"}

# Default period for backtesting
DEFAULT_PERIOD = "1y"

# Three-stage testing configuration
THREE_STAGE_CONFIG = {
    "stage1_name": "In-Sample Optimization",
    "stage2_name": "Walk-Forward Validation",
    "stage3_name": "Holdout Test + Regime Analysis",
    "train_ratio": 0.8,
    "wf_train_days": 90,
    "wf_test_days": 30,
}


# =============================================================================
# MODULE IMPORT HELPER
# =============================================================================


def import_local_module(module_name: str, file_path: Path, register_as: str = None):
    """Import a module from a local file path without triggering __init__.py.

    Args:
        module_name: Internal module name for importlib.
        file_path: Absolute path to the .py file.
        register_as: Optional dotted name to register in sys.modules (e.g.
            ``"strategy.strategies.hurst_kalman.core"``).

    Returns:
        The imported module object.
    """
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if register_as:
        sys.modules[register_as] = module
    spec.loader.exec_module(module)
    return module


# =============================================================================
# DATA FETCHING
# =============================================================================


async def fetch_data(
    symbol: str = "BTC/USDT:USDT",
    start_date: datetime = None,
    end_date: datetime = None,
    interval: KlineInterval = KlineInterval.MINUTE_15,
    exchange: str = "bitget",
    *,
    no_cache: bool = False,
    validate: bool = True,
    validate_sources: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV data.

    Args:
        symbol: Trading pair symbol.
        start_date: Start of data range (defaults to 2 years ago).
        end_date: End of data range (defaults to now).
        interval: Kline interval.
        exchange: Exchange name for CCXT provider.
        no_cache: If ``True``, bypass the local SQLite cache and fetch
            directly from the exchange.
        validate: If ``True`` (default), automatically cross-validate
            newly fetched data against a second exchange.  Already-cached
            data is assumed to have been validated on first fetch.
        validate_sources: Exchanges to use for cross-validation
            (default ``["okx"]``).  Binance/Bybit are blocked in China;
            usable alternatives: okx, gate, htx.

    Returns:
        OHLCV DataFrame with DatetimeIndex.
    """
    if start_date is None:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=365 * 2
        )
    if end_date is None:
        end_date = datetime.now(timezone.utc).replace(tzinfo=None)

    print(f"Fetching data from {start_date.date()} to {end_date.date()}...")

    if no_cache:
        async with CCXTDataProvider(exchange=exchange) as provider:
            data = await provider.fetch_klines(
                symbol=symbol,
                interval=interval,
                start=start_date,
                end=end_date,
            )
            print(f"Fetched {len(data)} bars")
            return data

    from nexustrader.backtest.data.cached_provider import (
        CachedDataProvider,
        _INTERVAL_STR,
    )

    # Build validation source list (avoid self-comparison)
    if validate_sources is None:
        validate_sources = ["okx"]
    validate_sources = [s for s in validate_sources if s != exchange]
    if validate and not validate_sources:
        validate_sources = ["gate"]

    all_exchanges = [exchange] + validate_sources
    provider = CachedDataProvider(exchanges=all_exchanges)
    try:
        # Check whether the primary exchange has gaps (needs fresh fetch)
        iv_str = _INTERVAL_STR.get(interval)
        has_gaps = True
        if iv_str:
            has_gaps = bool(
                provider._db.get_gaps(exchange, symbol, iv_str, start_date, end_date)
            )

        if has_gaps and validate and validate_sources:
            # New data to fetch — cross-validate with second source
            sources = [exchange] + validate_sources
            result = await provider.fetch_and_validate(
                symbol=symbol,
                interval=interval,
                start=start_date,
                end=end_date,
                sources=sources,
            )
            if result.is_valid:
                print("[validate] Cross-validation PASSED")
            else:
                print("[validate] Cross-validation WARNING — anomalies detected:")
                for src, info in result.validation_report.items():
                    if isinstance(info, dict) and "max_diff_pct" in info:
                        print(
                            f"  {src}: max_diff={info['max_diff_pct']:.4f}%, "
                            f"corr={info.get('correlation', 0):.6f}"
                        )
                if not result.anomalies.empty:
                    print(f"  {len(result.anomalies)} bar(s) with >1% deviation")
            data = result.primary_data
        else:
            # Cache hit or validation disabled — fetch from cache only
            data = await provider.fetch(
                symbol=symbol,
                interval=interval,
                start=start_date,
                end=end_date,
                exchange=exchange,
            )

        print(f"Loaded {len(data)} bars")
        return data
    finally:
        provider.close()


async def validate_data(
    symbol: str = "BTC/USDT:USDT",
    start_date: datetime = None,
    end_date: datetime = None,
    interval: KlineInterval = KlineInterval.MINUTE_15,
    sources: list[str] | None = None,
):
    """Fetch and cross-validate OHLCV data from multiple exchanges.

    Args:
        symbol: Trading pair symbol.
        start_date: Start of data range (defaults to 2 years ago).
        end_date: End of data range (defaults to now).
        interval: Kline interval.
        sources: List of exchange names to compare.

    Returns:
        :class:`~nexustrader.backtest.data.cached_provider.ValidatedData`.
    """
    from nexustrader.backtest.data.cached_provider import CachedDataProvider

    if start_date is None:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=365 * 2
        )
    if end_date is None:
        end_date = datetime.now(timezone.utc).replace(tzinfo=None)
    if sources is None:
        sources = ["bitget", "okx"]

    print(f"Validating data across {sources}...")

    provider = CachedDataProvider(exchanges=sources)
    try:
        result = await provider.fetch_and_validate(
            symbol=symbol,
            interval=interval,
            start=start_date,
            end=end_date,
            sources=sources,
        )
        if result.is_valid:
            print("[validate] Data quality: PASS")
        else:
            print("[validate] Data quality: WARN — anomalies detected")
            for src, info in result.validation_report.items():
                if isinstance(info, dict) and "max_diff_pct" in info:
                    print(
                        f"  {src}: max_diff={info['max_diff_pct']:.4f}%, "
                        f"corr={info.get('correlation', 0):.6f}"
                    )
            if not result.anomalies.empty:
                print(f"  {len(result.anomalies)} anomalous bar(s)")
        return result
    finally:
        provider.close()


async def fetch_funding_rates(
    symbol: str = "BTC/USDT:USDT",
    start_date: datetime = None,
    end_date: datetime = None,
    exchange: str = "bitget",
) -> pd.DataFrame:
    """Fetch historical funding rates.

    Tries the requested exchange first; if it returns fewer than
    ``_MIN_FUNDING_RECORDS`` records, falls back to Gate.io which
    provides full history for most perpetual pairs.

    Args:
        symbol: Trading pair symbol.
        start_date: Start of data range (defaults to 2 years ago).
        end_date: End of data range (defaults to now).
        exchange: Exchange name for FundingRateProvider.

    Returns:
        DataFrame with ``funding_rate`` column and DatetimeIndex.
    """
    _MIN_FUNDING_RECORDS = 500
    _FALLBACK_EXCHANGE = "gate"

    if start_date is None:
        start_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            days=365 * 2
        )
    if end_date is None:
        end_date = datetime.now(timezone.utc).replace(tzinfo=None)

    days_requested = (end_date - start_date).days
    # Only expect many records for long periods (>6 months = ~540 settlements)
    need_fallback_check = days_requested > 180

    print(f"Fetching funding rates from {exchange}...")

    try:
        async with FundingRateProvider(exchange=exchange) as provider:
            funding_rates = await provider.fetch_funding_rates(
                symbol=symbol,
                start=start_date,
                end=end_date,
            )

        if not funding_rates.empty:
            print(f"Fetched {len(funding_rates)} funding rate records from {exchange}")
            avg_rate = funding_rates["funding_rate"].mean() * 100
            print(f"Average funding rate: {avg_rate:.4f}% per 8h")

        # Fallback to gate if primary exchange returned too few records
        if (
            need_fallback_check
            and len(funding_rates) < _MIN_FUNDING_RECORDS
            and exchange != _FALLBACK_EXCHANGE
        ):
            print(
                f"[funding] {exchange} returned only {len(funding_rates)} records "
                f"for {days_requested}d — falling back to {_FALLBACK_EXCHANGE}..."
            )
            try:
                async with FundingRateProvider(
                    exchange=_FALLBACK_EXCHANGE
                ) as fallback:
                    fb_rates = await fallback.fetch_funding_rates(
                        symbol=symbol,
                        start=start_date,
                        end=end_date,
                    )
                if not fb_rates.empty and len(fb_rates) > len(funding_rates):
                    funding_rates = fb_rates
                    print(
                        f"[funding] Got {len(funding_rates)} records from "
                        f"{_FALLBACK_EXCHANGE}"
                    )
                    avg_rate = funding_rates["funding_rate"].mean() * 100
                    print(f"Average funding rate: {avg_rate:.4f}% per 8h")
            except Exception as e:
                print(f"[funding] Fallback to {_FALLBACK_EXCHANGE} failed: {e}")

        if funding_rates.empty:
            print("No funding rate data available (will use zero)")

        return funding_rates
    except Exception as e:
        print(f"Warning: Could not fetch funding rates: {e}")
        print("Continuing without funding rate data...")
        return pd.DataFrame(columns=["funding_rate"])


# =============================================================================
# CONFIG FACTORIES
# =============================================================================


def create_backtest_config(
    data: pd.DataFrame,
    symbol: str = "BTC/USDT:USDT",
    interval: KlineInterval = KlineInterval.MINUTE_15,
    initial_capital: float = 10000.0,
) -> BacktestConfig:
    """Create a standard backtest configuration from data.

    Args:
        data: OHLCV DataFrame (must have DatetimeIndex).
        symbol: Trading pair symbol.
        interval: Kline interval.
        initial_capital: Starting capital in USDT.
    """
    return BacktestConfig(
        symbol=symbol,
        interval=interval,
        start_date=data.index[0].to_pydatetime(),
        end_date=data.index[-1].to_pydatetime(),
        initial_capital=initial_capital,
    )


def create_cost_config(
    maker_fee: float = 0.0002,
    taker_fee: float = 0.0005,
    slippage_pct: float = 0.0005,
    use_funding_rate: bool = True,
) -> CostConfig:
    """Create a standard cost configuration.

    Args:
        maker_fee: Maker fee rate.
        taker_fee: Taker fee rate.
        slippage_pct: Estimated slippage percentage.
        use_funding_rate: Whether to apply funding rate costs.
    """
    return CostConfig(
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        slippage_pct=slippage_pct,
        use_funding_rate=use_funding_rate,
    )


# =============================================================================
# RESULT PERSISTENCE
# =============================================================================


def load_results(results_file: Path) -> Dict[str, Any]:
    """Load saved backtest results from a JSON file.

    Args:
        results_file: Path to the results JSON.

    Returns:
        Dictionary of saved results (empty dict if file doesn't exist).
    """
    if results_file.exists():
        with open(results_file) as f:
            return json.load(f)
    return {}


def save_results(results: Dict[str, Any], results_file: Path) -> None:
    """Save backtest results to a JSON file.

    Args:
        results: Results dictionary to persist.
        results_file: Destination path.
    """
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {results_file}")


def print_results_table(results: Dict[str, Any]) -> None:
    """Print results in a formatted table.

    Args:
        results: Dictionary of saved backtest results.
    """
    if not results:
        print("No saved results found.")
        return

    print("\n" + "=" * 95)
    print("BACKTEST RESULTS SUMMARY")
    print("=" * 95)

    # Group by period
    for period in ["3m", "6m", "1y", "2y"]:
        period_results = [
            (k, v)
            for k, v in results.items()
            if k.endswith(f"_{period}") or v.get("period") == period
        ]
        if not period_results:
            continue

        period_name = {
            "3m": "3 MONTHS",
            "6m": "6 MONTHS",
            "1y": "1 YEAR",
            "2y": "2 YEARS",
        }.get(period, period)
        print(f"\n{period_name}")
        print("-" * 95)
        print(
            f"{'Config':<8} {'Name':<24} {'Return':>10} {'Sharpe':>8} {'MaxDD':>10} {'WinRate':>10} {'Trades':>8}"
        )
        print("-" * 95)

        for key, data in sorted(
            period_results, key=lambda x: x[1].get("mesa_index", 0)
        ):
            mesa_idx = data.get("mesa_index", "?")
            name = data.get("config_name", "Custom")[:22]
            ret = data.get("total_return_pct", 0)
            sharpe = data.get("sharpe_ratio", 0) or 0
            dd = data.get("max_drawdown_pct", 0)
            win = data.get("win_rate_pct", 0)
            trades = data.get("total_trades", 0)

            marker = (
                " ***"
                if ret > 50 and trades >= 10
                else " **"
                if ret > 20 and trades >= 5
                else ""
            )
            print(
                f"M#{mesa_idx:<6} {name:<24} {ret:>+9.1f}% {sharpe:>7.2f} {dd:>9.1f}% {win:>9.1f}% {trades:>8}{marker}"
            )

    # Print optimization results if available
    opt_results = {k: v for k, v in results.items() if k.startswith("opt_")}
    if opt_results:
        print(f"\n{'OPTIMIZATION RESULTS'}")
        print("-" * 95)
        print(
            f"{'Period':<8} {'Param1':>8} {'Param2':>8} {'Return':>10} {'Sharpe':>8} {'MaxDD':>10} {'Trades':>8}"
        )
        print("-" * 95)
        for key, data in opt_results.items():
            period = data.get("period", key.replace("opt_", ""))
            ret = data.get("total_return_pct", 0)
            sharpe = data.get("sharpe_ratio", 0) or 0
            dd = data.get("max_drawdown_pct", 0)
            trades = data.get("total_trades", 0)
            print(
                f"{period:<8} {'':>8} {'':>8} {ret:>+9.1f}% {sharpe:>7.2f} {dd:>9.1f}% {trades:>8}"
            )

    print("\n" + "=" * 95)

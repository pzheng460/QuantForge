"""
Hurst-Kalman Strategy Backtest System.

Complete backtest pipeline with:
- Single configuration backtesting
- Grid search parameter optimization
- Walk-forward validation
- Market regime analysis
- Three-stage complete validation
- JSON result persistence
- Config export for paper trading

Usage:
    # Basic backtest with level 2 config (default 1 year)
    uv run python strategy/bitget/hurst_kalman/backtest.py

    # Test specific level (1-5) and period
    uv run python strategy/bitget/hurst_kalman/backtest.py --level 3 --period 6m

    # Complete three-stage test (recommended for production validation)
    uv run python strategy/bitget/hurst_kalman/backtest.py --full --period 1y

    # Three-stage test with config export
    uv run python strategy/bitget/hurst_kalman/backtest.py --full --export-config

    # Grid search optimization only
    uv run python strategy/bitget/hurst_kalman/backtest.py --optimize

    # Walk-forward validation only
    uv run python strategy/bitget/hurst_kalman/backtest.py --walk-forward

    # Show saved results
    uv run python strategy/bitget/hurst_kalman/backtest.py --show-results

Three-Stage Testing (--full):
    Stage 1: In-sample optimization (80% data) - Grid search for best parameters
    Stage 2: Walk-forward validation - Rolling window validation
    Stage 3: Holdout test (20% data) + Regime analysis - Final out-of-sample validation

Period Options:
    3m  = 3 months (90 days)
    6m  = 6 months (180 days)
    1y  = 1 year (365 days) [default]
    2y  = 2 years (730 days)
"""

import sys
from pathlib import Path
import importlib.util

# Add project root to path for direct script execution
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _import_local_module(module_name: str, file_path: Path, register_as: str = None):
    """Import a module from a local file path without triggering __init__.py."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    if register_as:
        sys.modules[register_as] = module
    spec.loader.exec_module(module)
    return module


# Import local modules directly to avoid __init__.py triggering strategy.py imports
# First import core.py and register it under the expected package name
_core = _import_local_module(
    "_hk_core",
    _SCRIPT_DIR / "core.py",
    register_as="strategy.bitget.hurst_kalman.core"
)

# Now configs.py can find core.py when it does `from strategy.bitget.hurst_kalman.core import ...`
_configs = _import_local_module(
    "_hk_configs",
    _SCRIPT_DIR / "configs.py",
    register_as="strategy.bitget.hurst_kalman.configs"
)

HurstKalmanConfig = _core.HurstKalmanConfig
KalmanFilter1D = _core.KalmanFilter1D
calculate_hurst = _core.calculate_hurst
TradeFilterConfig = _configs.TradeFilterConfig
get_config = _configs.get_config

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
from collections import deque  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from typing import Any, Dict, Optional  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from nexustrader.backtest import (  # noqa: E402
    BacktestConfig,
    ComprehensiveReportGenerator,
    CostConfig,
    GridSearchOptimizer,
    ParameterGrid,
    PerformanceAnalyzer,
    RegimeClassifier,
    ReportGenerator,
    Signal,
    VectorizedBacktest,
    WalkForwardAnalyzer,
    WindowType,
)
from nexustrader.backtest.data.ccxt_provider import CCXTDataProvider  # noqa: E402
from nexustrader.backtest.data.funding_rate import FundingRateProvider  # noqa: E402
from nexustrader.constants import KlineInterval  # noqa: E402


# =============================================================================
# CONSTANTS
# =============================================================================

RESULTS_FILE = Path(__file__).parent / "backtest_results.json"
REPORT_FILE = Path(__file__).parent / "backtest_report.html"

# Period options (3 months to 2 years)
PERIODS = {
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
}

# Default periods for three-stage testing
DEFAULT_PERIOD = "1y"  # Default to 1 year

# Three-stage testing configuration
# Stage 1: In-sample optimization (Grid Search) - uses 80% of data
# Stage 2: Walk-forward validation (Rolling OOS) - validates with sliding windows
# Stage 3: Holdout test + Regime analysis - final 20% holdout + market regime stats
THREE_STAGE_CONFIG = {
    "stage1_name": "样本内优化 (In-Sample Optimization)",
    "stage2_name": "滚动验证 (Walk-Forward Validation)",
    "stage3_name": "样本外测试 (Holdout Test + Regime Analysis)",
    "train_ratio": 0.8,  # 80% for training, 20% holdout
    "wf_train_days": 30,  # Walk-forward training window (days)
    "wf_test_days": 7,  # Walk-forward test window (days)
}


# =============================================================================
# RESULT PERSISTENCE
# =============================================================================

def load_results() -> Dict[str, Any]:
    """Load saved backtest results from JSON file."""
    if RESULTS_FILE.exists():
        with open(RESULTS_FILE) as f:
            return json.load(f)
    return {}


def save_results(results: Dict[str, Any]) -> None:
    """Save backtest results to JSON file."""
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {RESULTS_FILE}")


def print_results_table(results: Dict[str, Any]) -> None:
    """Print results in a formatted table."""
    if not results:
        print("No saved results found.")
        return

    print("\n" + "=" * 95)
    print("BACKTEST RESULTS SUMMARY (UQSS Tiering)")
    print("=" * 95)

    # UQSS level names
    level_names = {
        1: "Macro",
        2: "Swing",
        3: "Intraday",
        4: "Scalp",
        5: "Sniper",
    }

    # Group by period
    periods_found = set()
    for key in results.keys():
        if "_" in key:
            parts = key.rsplit("_", 1)
            if len(parts) == 2:
                periods_found.add(parts[1])

    for period in ["3m", "6m", "1y", "2y"]:
        period_results = [
            (k, v) for k, v in results.items()
            if k.endswith(f"_{period}") or v.get("period") == period
        ]
        if not period_results:
            continue

        period_name = {"3m": "3 MONTHS", "6m": "6 MONTHS", "1y": "1 YEAR", "2y": "2 YEARS"}.get(period, period)
        print(f"\n{period_name}")
        print("-" * 95)
        print(f"{'Level':<8} {'UQSS Tier':<18} {'Return':>10} {'Sharpe':>8} {'MaxDD':>10} {'WinRate':>10} {'Trades':>8}")
        print("-" * 95)

        for key, data in sorted(period_results, key=lambda x: x[1].get("config_level", 0)):
            level = data.get("config_level", "?")
            name = level_names.get(level, data.get("config_name", "Custom")[:16])
            ret = data.get("total_return_pct", 0)
            sharpe = data.get("sharpe_ratio", 0) or 0
            dd = data.get("max_drawdown_pct", 0)
            win = data.get("win_rate_pct", 0)
            trades = data.get("total_trades", 0)

            rec = " [REC]" if level == 2 else ""
            marker = " ***" if ret > 50 and trades >= 10 else " **" if ret > 20 and trades >= 5 else ""
            print(f"L{level}      {name:<18}{rec:<6} {ret:>+9.1f}% {sharpe:>7.2f} {dd:>9.1f}% {win:>9.1f}% {trades:>8}{marker}")

    # Print optimization results if available
    opt_results = {k: v for k, v in results.items() if k.startswith("opt_")}
    if opt_results:
        print(f"\n{'OPTIMIZATION RESULTS'}")
        print("-" * 95)
        print(f"{'Period':<8} {'Z-Score':>8} {'Hurst':>8} {'Return':>10} {'Sharpe':>8} {'MaxDD':>10} {'Trades':>8}")
        print("-" * 95)
        for key, data in opt_results.items():
            period = data.get("period", key.replace("opt_", ""))
            zscore = data.get("zscore_entry", 0)
            hurst = data.get("mean_reversion_threshold", 0)
            ret = data.get("total_return_pct", 0)
            sharpe = data.get("sharpe_ratio", 0) or 0
            dd = data.get("max_drawdown_pct", 0)
            trades = data.get("total_trades", 0)
            print(f"{period:<8} {zscore:>8.1f} {hurst:>8.2f} {ret:>+9.1f}% {sharpe:>7.2f} {dd:>9.1f}% {trades:>8}")

    print("\n" + "=" * 95)


# =============================================================================
# SIGNAL GENERATION
# =============================================================================

class HurstKalmanSignalGenerator:
    """
    Generate trading signals for vectorized backtest.

    Replicates the logic from HurstKalmanIndicator for backtesting.
    """

    def __init__(self, config: HurstKalmanConfig, filter_config: TradeFilterConfig):
        self.config = config
        self.filter = filter_config

    def generate(self, data: pd.DataFrame, params: Optional[Dict] = None) -> np.ndarray:
        """
        Generate signals from OHLCV data.

        Args:
            data: OHLCV DataFrame
            params: Optional parameter overrides

        Returns:
            Array of signal values (0=HOLD, 1=BUY, -1=SELL, 2=CLOSE)
        """
        # Apply parameter overrides
        hurst_window = params.get("hurst_window", self.config.hurst_window) if params else self.config.hurst_window
        zscore_window = params.get("zscore_window", self.config.zscore_window) if params else self.config.zscore_window
        zscore_entry = params.get("zscore_entry", self.config.zscore_entry) if params else self.config.zscore_entry
        mean_reversion_threshold = params.get("mean_reversion_threshold", self.config.mean_reversion_threshold) if params else self.config.mean_reversion_threshold
        kalman_R = params.get("kalman_R", self.config.kalman_R) if params else self.config.kalman_R
        kalman_Q = params.get("kalman_Q", self.config.kalman_Q) if params else self.config.kalman_Q

        n = len(data)
        signals = np.zeros(n)
        prices = data["close"].values

        # Initialize Kalman filter
        kalman = KalmanFilter1D(R=kalman_R, Q=kalman_Q)
        kalman_prices = []

        # Price history for Hurst
        price_history = deque(maxlen=hurst_window + 50)

        # State tracking
        min_holding_bars = self.filter.min_holding_bars
        cooldown_bars = self.filter.cooldown_bars
        only_mean_reversion = self.filter.only_mean_reversion

        position = 0  # 0=flat, 1=long, -1=short
        entry_bar = 0
        cooldown_until = 0
        signal_count = {Signal.BUY.value: 0, Signal.SELL.value: 0}

        for i in range(n):
            price = prices[i]
            price_history.append(price)

            # Update Kalman
            kalman_price = kalman.update(price)
            kalman_prices.append(kalman_price)

            # Skip warmup
            if i < hurst_window + zscore_window:
                continue

            # Calculate Hurst
            hurst = calculate_hurst(np.array(price_history), hurst_window)

            # Calculate Z-Score
            recent_prices = np.array(list(price_history)[-zscore_window:])
            recent_kalman = np.array(kalman_prices[-zscore_window:])
            deviations = recent_prices - recent_kalman
            std = np.std(deviations)
            zscore = (price - kalman_price) / std if std > 1e-10 else 0.0

            # Determine market state
            is_mean_reverting = hurst < mean_reversion_threshold

            # Skip if not in mean-reversion mode (if configured)
            if only_mean_reversion and not is_mean_reverting:
                # Close position when leaving mean-reversion
                if position != 0 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                continue

            # Skip if in cooldown
            if i < cooldown_until:
                continue

            # Generate raw signal
            raw_signal = Signal.HOLD.value
            if is_mean_reverting:
                if zscore < -zscore_entry:
                    raw_signal = Signal.BUY.value
                elif zscore > zscore_entry:
                    raw_signal = Signal.SELL.value
                elif abs(zscore) < 0.5 and position != 0:
                    raw_signal = Signal.CLOSE.value

            # Signal confirmation
            if raw_signal == Signal.BUY.value:
                signal_count[Signal.BUY.value] += 1
                signal_count[Signal.SELL.value] = 0
            elif raw_signal == Signal.SELL.value:
                signal_count[Signal.SELL.value] += 1
                signal_count[Signal.BUY.value] = 0
            else:
                signal_count[Signal.BUY.value] = 0
                signal_count[Signal.SELL.value] = 0

            # Check confirmation threshold
            confirmed_signal = Signal.HOLD.value
            if signal_count[Signal.BUY.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.BUY.value
            elif signal_count[Signal.SELL.value] >= self.filter.signal_confirmation:
                confirmed_signal = Signal.SELL.value
            elif raw_signal == Signal.CLOSE.value:
                confirmed_signal = Signal.CLOSE.value

            # Apply position logic
            if confirmed_signal == Signal.BUY.value:
                if position == -1 and i - entry_bar >= min_holding_bars:
                    # Close short first
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.BUY.value
                    position = 1
                    entry_bar = i

            elif confirmed_signal == Signal.SELL.value:
                if position == 1 and i - entry_bar >= min_holding_bars:
                    # Close long first
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars
                elif position == 0:
                    signals[i] = Signal.SELL.value
                    position = -1
                    entry_bar = i

            elif confirmed_signal == Signal.CLOSE.value:
                if position != 0 and i - entry_bar >= min_holding_bars:
                    signals[i] = Signal.CLOSE.value
                    position = 0
                    cooldown_until = i + cooldown_bars

        return signals


# =============================================================================
# DATA FETCHING
# =============================================================================

async def fetch_data(
    symbol: str = "BTC/USDT:USDT",
    start_date: datetime = None,
    end_date: datetime = None,
    interval: KlineInterval = KlineInterval.MINUTE_15,
) -> pd.DataFrame:
    """Fetch historical data from Bitget."""
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365 * 2)
    if end_date is None:
        end_date = datetime.now()

    print(f"Fetching data from {start_date.date()} to {end_date.date()}...")

    async with CCXTDataProvider(exchange="bitget") as provider:
        data = await provider.fetch_klines(
            symbol=symbol,
            interval=interval,
            start=start_date,
            end=end_date,
        )
        print(f"Fetched {len(data)} bars")
        return data


async def fetch_funding_rates(
    symbol: str = "BTC/USDT:USDT",
    start_date: datetime = None,
    end_date: datetime = None,
) -> pd.DataFrame:
    """Fetch historical funding rates from Bitget."""
    if start_date is None:
        start_date = datetime.now() - timedelta(days=365 * 2)
    if end_date is None:
        end_date = datetime.now()

    print("Fetching funding rates...")

    try:
        async with FundingRateProvider(exchange="bitget") as provider:
            funding_rates = await provider.fetch_funding_rates(
                symbol=symbol,
                start=start_date,
                end=end_date,
            )
            if not funding_rates.empty:
                print(f"Fetched {len(funding_rates)} funding rate records")
                # Show sample funding rates
                avg_rate = funding_rates["funding_rate"].mean() * 100
                print(f"Average funding rate: {avg_rate:.4f}% per 8h")
            else:
                print("No funding rate data available (will use zero)")
            return funding_rates
    except Exception as e:
        print(f"Warning: Could not fetch funding rates: {e}")
        print("Continuing without funding rate data...")
        return pd.DataFrame(columns=["funding_rate"])


# =============================================================================
# BACKTEST FUNCTIONS
# =============================================================================

def create_backtest_config(data: pd.DataFrame) -> BacktestConfig:
    """Create standard backtest configuration."""
    return BacktestConfig(
        symbol="BTC/USDT:USDT",
        interval=KlineInterval.MINUTE_15,
        start_date=data.index[0].to_pydatetime(),
        end_date=data.index[-1].to_pydatetime(),
        initial_capital=10000.0,
    )


def create_cost_config() -> CostConfig:
    """Create standard cost configuration for Bitget."""
    return CostConfig(
        maker_fee=0.0002,
        taker_fee=0.0005,
        slippage_pct=0.0005,
        use_funding_rate=True,  # Enable real funding rate
    )


def run_single_backtest(
    data: pd.DataFrame,
    config_level: int = 2,
    period: str = None,
    funding_rates: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Run backtest with a single configuration."""
    strategy_config = get_config(config_level)
    hk_config, filter_config = strategy_config.get_configs()

    if len(data) == 0:
        print("No data in specified date range")
        return {}

    bt_config = create_backtest_config(data)
    cost_config = create_cost_config()

    # Generate signals
    generator = HurstKalmanSignalGenerator(hk_config, filter_config)
    signals = generator.generate(data)

    # Run backtest with funding rates
    bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)
    result = bt.run(data=data, signals=signals, funding_rates=funding_rates)

    # Enhanced metrics
    analyzer = PerformanceAnalyzer(
        equity_curve=result.equity_curve,
        trades=result.trades,
        initial_capital=bt_config.initial_capital,
    )
    metrics = analyzer.calculate_metrics()

    # Get funding paid from backtest result (not from PerformanceAnalyzer)
    funding_paid = result.metrics.get('total_funding_paid', 0)
    print(f"\n{'='*60}")
    print(f"BACKTEST RESULTS - L{config_level} {strategy_config.name} ({strategy_config.level.name})")
    print(f"{'='*60}")
    print(f"Period: {data.index[0].date()} to {data.index[-1].date()}")
    print(f"Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"Max Drawdown: {metrics['max_drawdown_pct']:.2f}%")
    print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
    print(f"Sortino Ratio: {metrics['sortino_ratio']:.2f}")
    print(f"Calmar Ratio: {metrics['calmar_ratio']:.2f}")
    print(f"Total Trades: {metrics['total_trades']}")
    print(f"Win Rate: {metrics['win_rate_pct']:.1f}%")
    print(f"Profit Factor: {metrics['profit_factor']:.2f}")
    if funding_paid != 0:
        print(f"Funding Paid: ${funding_paid:.2f}")
    print(f"{'='*60}")

    return {
        "config_level": config_level,
        "config_name": strategy_config.name,
        "period": period,
        "start_date": str(data.index[0].date()),
        "end_date": str(data.index[-1].date()),
        "run_time": datetime.now().isoformat(),
        "total_return_pct": round(metrics["total_return_pct"], 2),
        "max_drawdown_pct": round(metrics["max_drawdown_pct"], 2),
        "sharpe_ratio": round(metrics["sharpe_ratio"], 2),
        "sortino_ratio": round(metrics["sortino_ratio"], 2),
        "calmar_ratio": round(metrics["calmar_ratio"], 2),
        "total_trades": metrics["total_trades"],
        "win_rate_pct": round(metrics["win_rate_pct"], 1),
        "profit_factor": round(metrics["profit_factor"], 2),
        "result": result,
        "data": data,
    }


def run_grid_search(
    data: pd.DataFrame,
    train_ratio: float = 0.8,
    period: str = None,
) -> Dict[str, Any]:
    """Run grid search optimization."""
    train_idx = int(len(data) * train_ratio)
    train_data = data.iloc[:train_idx]

    print(f"\n{'='*60}")
    print("GRID SEARCH OPTIMIZATION")
    print(f"{'='*60}")
    print(f"Training period: {train_data.index[0].date()} to {train_data.index[-1].date()}")
    print(f"Training bars: {len(train_data)}")

    bt_config = create_backtest_config(train_data)
    cost_config = create_cost_config()

    # Base config for signal generator
    base_config = HurstKalmanConfig()
    base_filter = TradeFilterConfig(only_mean_reversion=True)
    generator = HurstKalmanSignalGenerator(base_config, base_filter)

    def signal_fn(df: pd.DataFrame, params: Dict) -> np.ndarray:
        return generator.generate(df, params)

    optimizer = GridSearchOptimizer(
        data=train_data,
        config=bt_config,
        signal_generator=signal_fn,
        cost_config=cost_config,
    )

    # Define parameter grid
    grid = ParameterGrid(
        hurst_window=[80, 100, 120],
        zscore_entry=[2.0, 2.5, 3.0, 3.5],
        mean_reversion_threshold=[0.35, 0.40, 0.45],
        kalman_R=[0.1, 0.2, 0.3],
    )

    print(f"Parameter combinations: {len(grid)}")
    print("Running optimization...")

    results = optimizer.optimize(grid, target_metric="sharpe_ratio")

    print("\nTop 10 Results:")
    print("-" * 80)
    df = optimizer.results_to_dataframe(results[:10])
    print(df.to_string())

    best_params = optimizer.get_best_params(results)
    best_metrics = results[0].metrics if results else {}

    print(f"\nBest Parameters: {best_params}")
    print(f"{'='*60}")

    return {
        "results": results,
        "best_params": best_params,
        "best_metrics": best_metrics,
        "train_data": train_data,
        "period": period,
    }


def run_walk_forward(
    data: pd.DataFrame,
    params: Dict = None,
) -> Dict[str, Any]:
    """Run walk-forward validation."""
    print(f"\n{'='*60}")
    print("WALK-FORWARD VALIDATION")
    print(f"{'='*60}")

    bt_config = create_backtest_config(data)
    cost_config = create_cost_config()

    # Use provided params or default
    base_config = HurstKalmanConfig(**(params or {}))
    base_filter = TradeFilterConfig(only_mean_reversion=True)
    generator = HurstKalmanSignalGenerator(base_config, base_filter)

    def signal_fn(df: pd.DataFrame, p: Dict) -> np.ndarray:
        merged_params = {**(params or {}), **p}
        return generator.generate(df, merged_params)

    # 30 days training, 7 days testing
    train_periods = 96 * 30
    test_periods = 96 * 7

    wf_analyzer = WalkForwardAnalyzer(
        data=data,
        config=bt_config,
        signal_generator=signal_fn,
        train_periods=train_periods,
        test_periods=test_periods,
        window_type=WindowType.ROLLING,
        cost_config=cost_config,
    )

    param_grid = ParameterGrid(dummy=[1])
    results = wf_analyzer.run(param_grid)
    summary = wf_analyzer.get_summary(results)

    print(f"Windows: {summary['windows_count']}")
    print(f"Avg Train Return: {summary['avg_train_return']:.2f}%")
    print(f"Avg Test Return: {summary['avg_test_return']:.2f}%")
    print(f"Robustness Ratio: {summary['robustness_ratio']:.2f}")
    print(f"Positive Test Windows: {summary['positive_test_windows']}/{summary['windows_count']}")
    print(f"Total Test Return: {summary['total_test_return']:.2f}%")
    print(f"{'='*60}")

    return {
        "results": results,
        "summary": summary,
    }


def run_regime_analysis(
    data: pd.DataFrame,
    result,
) -> Dict[str, Any]:
    """Run market regime analysis."""
    print(f"\n{'='*60}")
    print("MARKET REGIME ANALYSIS")
    print(f"{'='*60}")

    classifier = RegimeClassifier(
        trend_threshold=0.02,
        volatility_threshold=2.0,
    )

    regimes = classifier.classify(data)
    performance = classifier.get_performance_by_regime(regimes, result.equity_curve)
    regime_summary = classifier.get_regime_summary(regimes)

    print("\nRegime Distribution:")
    for regime, pct in regime_summary.items():
        print(f"  {regime}: {pct:.1f}%")

    print("\nPerformance by Regime:")
    for regime, metrics in performance.items():
        print(f"  {regime}: {metrics['return_pct']:+.2f}% return ({metrics['count']} bars)")

    print(f"{'='*60}")

    return {
        "regimes": regimes,
        "performance": performance,
        "summary": regime_summary,
    }


def run_three_stage_test(
    data: pd.DataFrame,
    funding_rates: Optional[pd.DataFrame] = None,
    period: str = DEFAULT_PERIOD,
    export_config_flag: bool = False,
) -> Dict[str, Any]:
    """
    Run complete three-stage backtest validation.

    Stage 1: In-sample optimization (Grid Search)
        - Uses 80% of data for parameter optimization
        - Identifies best parameters based on Sharpe ratio

    Stage 2: Walk-forward validation
        - Rolling window validation with train/test splits
        - Validates parameter stability over time

    Stage 3: Holdout test + Regime analysis
        - Tests on final 20% of data (never seen during optimization)
        - Analyzes performance by market regime

    Args:
        data: OHLCV DataFrame
        funding_rates: Optional funding rate DataFrame
        period: Period string for reporting
        export_config_flag: Whether to export best config

    Returns:
        Dictionary with all three stages' results
    """
    print("\n" + "=" * 80)
    print("三阶段完整回测验证 (Three-Stage Backtest Validation)")
    print("=" * 80)
    print(f"数据周期: {data.index[0].date()} to {data.index[-1].date()}")
    print(f"总数据条数: {len(data)} bars")
    print(f"时间段: {period}")
    print("=" * 80)

    # Split data
    train_ratio = THREE_STAGE_CONFIG["train_ratio"]
    split_idx = int(len(data) * train_ratio)
    train_data = data.iloc[:split_idx]
    holdout_data = data.iloc[split_idx:]

    print("\n数据划分:")
    print(f"  训练集 (80%): {train_data.index[0].date()} to {train_data.index[-1].date()} ({len(train_data)} bars)")
    print(f"  样本外 (20%): {holdout_data.index[0].date()} to {holdout_data.index[-1].date()} ({len(holdout_data)} bars)")

    results = {}

    # =========================================================================
    # STAGE 1: In-sample optimization
    # =========================================================================
    print("\n" + "=" * 80)
    print(f"阶段 1: {THREE_STAGE_CONFIG['stage1_name']}")
    print("=" * 80)

    opt_result = run_grid_search(train_data, train_ratio=1.0, period=period)
    best_params = opt_result["best_params"]
    best_metrics = opt_result["best_metrics"]

    results["stage1"] = {
        "name": THREE_STAGE_CONFIG["stage1_name"],
        "best_params": best_params,
        "in_sample_return": best_metrics.get("total_return_pct", 0),
        "in_sample_sharpe": best_metrics.get("sharpe_ratio", 0),
        "in_sample_drawdown": best_metrics.get("max_drawdown_pct", 0),
        "in_sample_trades": best_metrics.get("total_trades", 0),
    }

    print("\n阶段 1 结果:")
    print(f"  最优参数: {best_params}")
    print(f"  样本内收益: {results['stage1']['in_sample_return']:+.2f}%")
    print(f"  样本内夏普: {results['stage1']['in_sample_sharpe']:.2f}")

    # =========================================================================
    # STAGE 2: Walk-forward validation
    # =========================================================================
    print("\n" + "=" * 80)
    print(f"阶段 2: {THREE_STAGE_CONFIG['stage2_name']}")
    print("=" * 80)

    wf_result = run_walk_forward(train_data, best_params)
    wf_summary = wf_result["summary"]

    results["stage2"] = {
        "name": THREE_STAGE_CONFIG["stage2_name"],
        "windows_count": wf_summary["windows_count"],
        "avg_train_return": wf_summary["avg_train_return"],
        "avg_test_return": wf_summary["avg_test_return"],
        "robustness_ratio": wf_summary["robustness_ratio"],
        "positive_windows": wf_summary["positive_test_windows"],
        "total_test_return": wf_summary["total_test_return"],
    }

    # Robustness check
    robustness_pass = wf_summary["robustness_ratio"] >= 0.5
    positive_pct = wf_summary["positive_test_windows"] / max(wf_summary["windows_count"], 1)
    consistency_pass = positive_pct >= 0.5

    print("\n阶段 2 结果:")
    print(f"  滚动窗口数: {wf_summary['windows_count']}")
    print(f"  鲁棒性比率: {wf_summary['robustness_ratio']:.2f} {'✓' if robustness_pass else '✗'} (>= 0.5)")
    print(f"  正收益窗口: {wf_summary['positive_test_windows']}/{wf_summary['windows_count']} ({positive_pct:.0%}) {'✓' if consistency_pass else '✗'} (>= 50%)")

    # =========================================================================
    # STAGE 3: Holdout test + Regime analysis
    # =========================================================================
    print("\n" + "=" * 80)
    print(f"阶段 3: {THREE_STAGE_CONFIG['stage3_name']}")
    print("=" * 80)

    # Run backtest on holdout data
    bt_config = create_backtest_config(holdout_data)
    cost_config = create_cost_config()

    base_config = HurstKalmanConfig(**best_params)
    base_filter = TradeFilterConfig(only_mean_reversion=True)
    generator = HurstKalmanSignalGenerator(base_config, base_filter)
    signals = generator.generate(holdout_data, best_params)

    bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)

    # Split funding rates for holdout period
    holdout_funding = None
    if funding_rates is not None and not funding_rates.empty:
        holdout_start = holdout_data.index[0]
        holdout_end = holdout_data.index[-1]
        holdout_funding = funding_rates[
            (funding_rates.index >= holdout_start) & (funding_rates.index <= holdout_end)
        ]

    holdout_result = bt.run(data=holdout_data, signals=signals, funding_rates=holdout_funding)

    analyzer = PerformanceAnalyzer(
        equity_curve=holdout_result.equity_curve,
        trades=holdout_result.trades,
        initial_capital=bt_config.initial_capital,
    )
    holdout_metrics = analyzer.calculate_metrics()

    # Regime analysis on holdout data
    regime_result = run_regime_analysis(holdout_data, holdout_result)

    results["stage3"] = {
        "name": THREE_STAGE_CONFIG["stage3_name"],
        "holdout_return": holdout_metrics["total_return_pct"],
        "holdout_sharpe": holdout_metrics["sharpe_ratio"],
        "holdout_drawdown": holdout_metrics["max_drawdown_pct"],
        "holdout_trades": holdout_metrics["total_trades"],
        "holdout_win_rate": holdout_metrics["win_rate_pct"],
        "regime_summary": regime_result["summary"],
        "regime_performance": {k: v["return_pct"] for k, v in regime_result["performance"].items()},
    }

    # Performance degradation check
    in_sample_return = results["stage1"]["in_sample_return"]
    holdout_return = results["stage3"]["holdout_return"]
    if in_sample_return > 0:
        degradation = 1 - (holdout_return / in_sample_return) if holdout_return >= 0 else 1.0
    else:
        degradation = 0 if holdout_return <= in_sample_return else 1.0

    degradation_pass = degradation <= 0.5  # Less than 50% degradation

    print("\n阶段 3 结果 (样本外测试):")
    print(f"  样本外收益: {holdout_return:+.2f}%")
    print(f"  样本外夏普: {holdout_metrics['sharpe_ratio']:.2f}")
    print(f"  最大回撤: {holdout_metrics['max_drawdown_pct']:.2f}%")
    print(f"  交易次数: {holdout_metrics['total_trades']}")
    print(f"  性能衰减: {degradation:.0%} {'✓' if degradation_pass else '✗'} (<= 50%)")

    # =========================================================================
    # Summary
    # =========================================================================
    print("\n" + "=" * 80)
    print("三阶段测试总结 (Three-Stage Test Summary)")
    print("=" * 80)

    # Overall pass/fail
    stage1_pass = results["stage1"]["in_sample_sharpe"] >= 1.0
    stage2_pass = robustness_pass and consistency_pass
    stage3_pass = degradation_pass and holdout_metrics["sharpe_ratio"] >= 0.5

    all_pass = stage1_pass and stage2_pass and stage3_pass

    print(f"\n{'阶段':<20} {'状态':<10} {'关键指标'}")
    print("-" * 60)
    print(f"{'阶段1: 样本内优化':<20} {'✓ 通过' if stage1_pass else '✗ 未通过':<10} Sharpe={results['stage1']['in_sample_sharpe']:.2f}")
    print(f"{'阶段2: 滚动验证':<20} {'✓ 通过' if stage2_pass else '✗ 未通过':<10} 鲁棒性={wf_summary['robustness_ratio']:.2f}")
    print(f"{'阶段3: 样本外测试':<20} {'✓ 通过' if stage3_pass else '✗ 未通过':<10} Sharpe={holdout_metrics['sharpe_ratio']:.2f}, 衰减={degradation:.0%}")
    print("-" * 60)
    print(f"{'整体评估':<20} {'✓ 策略可用' if all_pass else '✗ 需要改进'}")

    results["summary"] = {
        "stage1_pass": stage1_pass,
        "stage2_pass": stage2_pass,
        "stage3_pass": stage3_pass,
        "all_pass": all_pass,
        "best_params": best_params,
        "in_sample_sharpe": results["stage1"]["in_sample_sharpe"],
        "holdout_sharpe": holdout_metrics["sharpe_ratio"],
        "robustness_ratio": wf_summary["robustness_ratio"],
        "degradation": degradation,
    }

    # Export config if requested and all stages pass
    if export_config_flag:
        config_code = export_config(best_params, holdout_metrics, period)
        print("\n" + "=" * 60)
        print("EXPORT CONFIG FOR PAPER TRADING")
        print("=" * 60)
        print(config_code)

        export_file = Path(__file__).parent / "optimized_config.py"
        with open(export_file, "w") as f:
            f.write("from strategy.bitget.hurst_kalman.configs import StrategyConfig, TradeFilterConfig\n")
            f.write("from strategy.bitget.hurst_kalman.core import HurstKalmanConfig\n")
            f.write(config_code)
        print(f"\nConfig saved to {export_file}")

    return results


def generate_report(result, output_path: Path = None):
    """Generate HTML report."""
    if output_path is None:
        output_path = REPORT_FILE

    print(f"\nGenerating report to {output_path}...")

    generator = ReportGenerator(result)
    generator.save(output_path)

    print(f"Report saved to {output_path}")


def export_config(params: Dict, metrics: Dict, period: str = None) -> str:
    """Export optimized parameters as config code for paper trading."""
    code = f'''
# =============================================================================
# OPTIMIZED CONFIG (Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")})
# Period: {period or "N/A"}
# Performance: {metrics.get("total_return_pct", 0):.1f}% return, {metrics.get("sharpe_ratio", 0):.2f} Sharpe
# =============================================================================

OPTIMIZED_CONFIG = StrategyConfig(
    name="Optimized",
    description="Auto-optimized parameters from grid search",
    risk_level="medium",
    recommended=False,
    strategy_config=HurstKalmanConfig(
        symbols=["BTCUSDT-PERP.BITGET"],
        hurst_window={params.get("hurst_window", 100)},
        zscore_window=60,
        zscore_entry={params.get("zscore_entry", 3.0)},
        mean_reversion_threshold={params.get("mean_reversion_threshold", 0.40)},
        trend_threshold=0.60,
        kalman_R={params.get("kalman_R", 0.2)},
        kalman_Q=5e-05,
        position_size_pct=0.10,
        stop_loss_pct=0.03,
        daily_loss_limit=0.03,
    ),
    filter_config=TradeFilterConfig(
        min_holding_bars=8,
        cooldown_bars=4,
        signal_confirmation=1,
        only_mean_reversion=True,
    ),
)

# To use in paper trading, add to configs.py and set:
# SELECTED_CONFIG = OPTIMIZED_CONFIG
'''
    return code


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Hurst-Kalman Strategy Backtest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
三阶段完整测试 (--full):
  阶段1: 样本内优化 - 使用80%数据进行网格搜索参数优化
  阶段2: 滚动验证 - Walk-forward validation验证参数稳定性
  阶段3: 样本外测试 - 使用20%样本外数据测试+市场状态分析

时间周期选项:
  3m  = 3个月 (90天)
  6m  = 6个月 (180天)
  1y  = 1年 (365天) [默认]
  2y  = 2年 (730天)
        """
    )
    parser.add_argument("--level", "-l", type=int, default=2, help="Config level 1-5 (default: 2)")
    parser.add_argument("--period", "-p", type=str, default=DEFAULT_PERIOD,
                        help=f"Period: 3m, 6m, 1y, 2y (default: {DEFAULT_PERIOD})")
    parser.add_argument("--optimize", "-o", action="store_true", help="Run grid search optimization only")
    parser.add_argument("--walk-forward", "-w", action="store_true", help="Run walk-forward validation only")
    parser.add_argument("--regime", "-r", action="store_true", help="Run regime analysis only")
    parser.add_argument("--full", "-f", action="store_true",
                        help="Run complete three-stage test (optimize + walk-forward + holdout)")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")
    parser.add_argument("--show-results", "-s", action="store_true", help="Show saved results")
    parser.add_argument("--export-config", "-e", action="store_true", help="Export best config for paper trading")
    parser.add_argument("--all-levels", "-a", action="store_true", help="Test all levels (1-5)")

    args = parser.parse_args()

    # Show saved results
    if args.show_results:
        results = load_results()
        print_results_table(results)
        return

    # Validate period
    if args.period not in PERIODS:
        print(f"Error: Invalid period '{args.period}'. Must be one of: {list(PERIODS.keys())}")
        return

    # Fetch data
    days = PERIODS[args.period]
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    data = await fetch_data(
        symbol="BTC/USDT:USDT",
        start_date=start_date,
        end_date=end_date,
    )

    # Fetch funding rates
    funding_rates = await fetch_funding_rates(
        symbol="BTC/USDT:USDT",
        start_date=start_date,
        end_date=end_date,
    )

    # BTC benchmark
    btc_return = (data["close"].iloc[-1] - data["close"].iloc[0]) / data["close"].iloc[0] * 100
    print(f"BTC Buy & Hold: {btc_return:+.1f}%")

    # Load existing results
    all_results = load_results()
    best_params = None
    bt_result = None

    # Full comprehensive testing mode
    if args.full:
        print("\n" + "=" * 80)
        print("完整回测验证模式 (Comprehensive Backtest Validation)")
        print("=" * 80)
        print("包含: 所有UQSS级别测试 + 三阶段验证 + 市场状态分析 + 完整报告")
        print("=" * 80)

        # Initialize comprehensive report generator
        report_gen = ComprehensiveReportGenerator(
            symbol="BTC/USDT:USDT",
            period=args.period,
        )
        report_gen.set_data_info(
            start_date=data.index[0],
            end_date=data.index[-1],
            total_bars=len(data),
            btc_return=btc_return,
        )
        if funding_rates is not None and not funding_rates.empty:
            avg_rate = funding_rates["funding_rate"].mean() * 100
            report_gen.set_funding_info(count=len(funding_rates), avg_rate=avg_rate)

        # Step 1: Test all UQSS levels
        print("\n" + "-" * 60)
        print("步骤 1: 测试所有 UQSS 级别 (L1-L5)")
        print("-" * 60)

        for level in [1, 2, 3, 4, 5]:
            result = run_single_backtest(
                data, config_level=level, period=args.period, funding_rates=funding_rates
            )
            if result:
                strategy_config = get_config(level)
                # Extract metrics from result (they are at top level, not under "metrics" key)
                level_metrics = {
                    "total_return_pct": result.get("total_return_pct", 0),
                    "sharpe_ratio": result.get("sharpe_ratio", 0),
                    "max_drawdown_pct": result.get("max_drawdown_pct", 0),
                    "win_rate_pct": result.get("win_rate_pct", 0),
                    "total_trades": result.get("total_trades", 0),
                    "sortino_ratio": result.get("sortino_ratio", 0),
                    "calmar_ratio": result.get("calmar_ratio", 0),
                    "profit_factor": result.get("profit_factor", 0),
                }
                report_gen.add_level_result(
                    level=level,
                    name=strategy_config.name,
                    metrics=level_metrics,
                )
                # Save to all_results
                key = f"{level}_{args.period}"
                result_to_save = {k: v for k, v in result.items() if k not in ["result", "data"]}
                all_results[key] = result_to_save

        # Step 2: Three-stage validation
        print("\n" + "-" * 60)
        print("步骤 2: 三阶段完整验证")
        print("-" * 60)

        three_stage_results = run_three_stage_test(
            data=data,
            funding_rates=funding_rates,
            period=args.period,
            export_config_flag=args.export_config,
        )
        report_gen.set_three_stage_results(three_stage_results)

        # Save three-stage results
        all_results[f"full_{args.period}"] = {
            "period": args.period,
            "run_time": datetime.now().isoformat(),
            "stage1_sharpe": three_stage_results["stage1"]["in_sample_sharpe"],
            "stage1_return": three_stage_results["stage1"]["in_sample_return"],
            "stage2_robustness": three_stage_results["stage2"]["robustness_ratio"],
            "stage3_sharpe": three_stage_results["stage3"]["holdout_sharpe"],
            "stage3_return": three_stage_results["stage3"]["holdout_return"],
            "all_pass": three_stage_results["summary"]["all_pass"],
            **three_stage_results["summary"]["best_params"],
        }

        # Step 3: Comprehensive regime analysis
        print("\n" + "-" * 60)
        print("步骤 3: 完整市场状态分析")
        print("-" * 60)

        # Run backtest with best params for regime analysis
        best_params = three_stage_results["summary"]["best_params"]
        base_config = HurstKalmanConfig(**best_params)
        base_filter = TradeFilterConfig(only_mean_reversion=True)
        generator = HurstKalmanSignalGenerator(base_config, base_filter)
        signals = generator.generate(data, best_params)

        bt_config = create_backtest_config(data)
        cost_config = create_cost_config()
        bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)
        result = bt.run(data=data, signals=signals, funding_rates=funding_rates)

        # Detailed regime analysis
        regime_result = run_regime_analysis(data, result)

        # Simple regime analysis (US-10 spec: bull/bear/ranging with 20% thresholds)
        classifier = RegimeClassifier()
        simple_regimes = classifier.classify_simple(data, bull_threshold=0.20, bear_threshold=0.20)
        simple_regime_summary = classifier.get_simple_regime_summary(simple_regimes)

        print("\n简化市场状态分析 (US-10 规格):")
        print(f"  牛市 (>20%): {simple_regime_summary.get('bull_pct', 0):.1f}%")
        print(f"  熊市 (<-20%): {simple_regime_summary.get('bear_pct', 0):.1f}%")
        print(f"  震荡: {simple_regime_summary.get('ranging_pct', 0):.1f}%")

        report_gen.set_regime_results(
            detailed_results=regime_result,
            simple_results=simple_regime_summary,
        )

        # Generate comprehensive HTML report
        report_path = Path(__file__).parent / "comprehensive_report.html"
        report_gen.save(report_path)
        print(f"\n完整报告已保存至: {report_path}")

        # Also generate standard report if requested
        if args.report:
            generate_report(result)

        # Save and print results
        save_results(all_results)
        print_results_table(all_results)
        print("\nDone!")
        return

    # Test all levels or single level
    levels_to_test = [1, 2, 3, 4, 5] if args.all_levels else [args.level]

    for level in levels_to_test:
        result = run_single_backtest(data, config_level=level, period=args.period, funding_rates=funding_rates)
        if result:
            key = f"{level}_{args.period}"
            # Don't store large objects in JSON
            result_to_save = {k: v for k, v in result.items() if k not in ["result", "data"]}
            all_results[key] = result_to_save

            if level == args.level:
                bt_result = result

    # Grid search optimization (standalone mode)
    if args.optimize:
        opt_result = run_grid_search(data, period=args.period)
        best_params = opt_result["best_params"]
        best_metrics = opt_result["best_metrics"]

        # Save optimization result
        all_results[f"opt_{args.period}"] = {
            "period": args.period,
            "run_time": datetime.now().isoformat(),
            **best_params,
            "total_return_pct": round(best_metrics.get("total_return_pct", 0), 2),
            "sharpe_ratio": round(best_metrics.get("sharpe_ratio", 0), 2),
            "max_drawdown_pct": round(best_metrics.get("max_drawdown_pct", 0), 2),
            "total_trades": best_metrics.get("total_trades", 0),
            "win_rate_pct": round(best_metrics.get("win_rate_pct", 0), 1),
        }

        # Re-run backtest with best params on full data
        print("\nRunning backtest with optimized parameters on full data...")
        base_config = HurstKalmanConfig(**best_params)
        base_filter = TradeFilterConfig(only_mean_reversion=True)
        generator = HurstKalmanSignalGenerator(base_config, base_filter)
        signals = generator.generate(data, best_params)

        bt_config = create_backtest_config(data)
        cost_config = create_cost_config()
        bt = VectorizedBacktest(config=bt_config, cost_config=cost_config)
        result = bt.run(data=data, signals=signals, funding_rates=funding_rates)

        analyzer = PerformanceAnalyzer(
            equity_curve=result.equity_curve,
            trades=result.trades,
            initial_capital=bt_config.initial_capital,
        )
        metrics = analyzer.calculate_metrics()

        print("\nOptimized Full-Data Results:")
        print(f"  Return: {metrics['total_return_pct']:+.2f}%")
        print(f"  Sharpe: {metrics['sharpe_ratio']:.2f}")
        print(f"  Trades: {metrics['total_trades']}")

        bt_result = {"result": result, "data": data}

        # Export config if requested
        if args.export_config:
            config_code = export_config(best_params, metrics, args.period)
            print("\n" + "=" * 60)
            print("EXPORT CONFIG FOR PAPER TRADING")
            print("=" * 60)
            print(config_code)

            # Save to file
            export_file = Path(__file__).parent / "optimized_config.py"
            with open(export_file, "w") as f:
                f.write("from strategy.bitget.hurst_kalman.configs import StrategyConfig, TradeFilterConfig\n")
                f.write("from strategy.bitget.hurst_kalman.core import HurstKalmanConfig\n")
                f.write(config_code)
            print(f"\nConfig saved to {export_file}")

    # Walk-forward validation
    if args.walk_forward:
        run_walk_forward(data, best_params)

    # Regime analysis
    if args.regime and bt_result and bt_result.get("result"):
        run_regime_analysis(data, bt_result["result"])

    # Generate report
    if args.report and bt_result and bt_result.get("result"):
        generate_report(bt_result["result"])

    # Save all results
    save_results(all_results)

    # Print summary table
    print_results_table(all_results)

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())

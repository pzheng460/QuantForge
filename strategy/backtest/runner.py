"""
Unified Backtest Runner.

Replaces the duplicated run_single_backtest / run_grid_search /
run_walk_forward / run_regime_analysis / run_three_stage_test /
generate_report / export_config functions that were copy-pasted
across three strategy backtest.py files.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from nexustrader.backtest import (
    BacktestConfig,
    GridSearchOptimizer,
    ParameterGrid,
    PerformanceAnalyzer,
    RegimeClassifier,
    ReportGenerator,
    VectorizedBacktest,
    WalkForwardAnalyzer,
    WindowType,
)
from nexustrader.constants import KlineInterval
from strategy.backtest.exchange_profiles import get_profile
from strategy.backtest.registry import get_strategy

# Re-use the common utilities that already exist
from strategy.backtest.utils import (
    DEFAULT_PERIOD,
    THREE_STAGE_CONFIG,
    load_results as _load_results,
    save_results as _save_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _apply_signal_delay(signals: np.ndarray) -> np.ndarray:
    """Shift signals right by 1 bar: signal from bar i executes at bar i+1."""
    delayed = np.empty_like(signals)
    delayed[0] = 0  # No trade on first bar
    delayed[1:] = signals[:-1]
    return delayed


def _bh_return_pct(data: pd.DataFrame, leverage: float = 1.0) -> float:
    """Buy-and-hold return for the period (leveraged)."""
    if len(data) < 2:
        return 0.0
    return (data["close"].iloc[-1] / data["close"].iloc[0] - 1) * 100 * leverage


def _bootstrap_sharpe_ci(
    equity_curve: pd.Series,
    periods_per_year: int,
    n_bootstrap: int = 500,
    block_size: int = None,
    seed: int = 42,
) -> tuple:
    """Block-bootstrap 95% CI for Sharpe ratio from an equity curve.

    Returns (lower_bound, upper_bound) or (None, None) if insufficient data.
    """
    returns = equity_curve.pct_change().dropna().values
    n = len(returns)
    if n < 10:
        return None, None
    if block_size is None:
        block_size = max(1, n // 50)
    rng = np.random.default_rng(seed)
    sharpes = []
    for _ in range(n_bootstrap):
        n_blocks = max(1, n // block_size + 1)
        starts = rng.integers(0, max(1, n - block_size + 1), size=n_blocks)
        resampled = np.concatenate([returns[s : s + block_size] for s in starts])[:n]
        std = resampled.std()
        if std > 1e-12:
            sharpes.append(resampled.mean() / std * np.sqrt(periods_per_year))
    if len(sharpes) < 10:
        return None, None
    arr = np.array(sharpes)
    return float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))


def _bars_per_day(interval: KlineInterval) -> int:
    """Return number of bars per calendar day for the given interval."""
    _MINUTES = {
        KlineInterval.MINUTE_1: 1,
        KlineInterval.MINUTE_5: 5,
        KlineInterval.MINUTE_15: 15,
        KlineInterval.MINUTE_30: 30,
        KlineInterval.HOUR_1: 60,
        KlineInterval.HOUR_4: 240,
        KlineInterval.HOUR_8: 480,
        KlineInterval.DAY_1: 1440,
    }
    mins = _MINUTES.get(interval, 15)
    return max(1, 1440 // mins)


def _get_position_size_pct(config) -> float:
    return float(getattr(config, "position_size_pct", 1.0))


class BacktestRunner:
    """Unified backtest runner parameterized by strategy registration + exchange profile."""

    def __init__(
        self,
        strategy_name: str,
        exchange: str = "bitget",
        symbol: str = None,
        output_dir: Path = None,
        leverage: float = 1.0,
        n_jobs: int = 1,
    ):
        self.reg = get_strategy(strategy_name)
        self.profile = get_profile(exchange)
        self.symbol = symbol or self.profile.default_symbol
        self.leverage = leverage
        self.n_jobs = n_jobs
        self.output_dir = output_dir or Path(
            f"strategy/results/{strategy_name}/{exchange}"
        )
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._results_file = self.output_dir / "backtest_results.json"
        self._report_file = self.output_dir / "backtest_report.html"

    # ------------------------------------------------------------------
    # Helper: build configs from Mesa or overrides
    # ------------------------------------------------------------------
    def _get_configs(
        self, mesa_index: int = 0, config_override=None, filter_override=None
    ):
        """Return (strategy_config_obj, filter_config_obj, strategy_config_or_None)."""
        if config_override and filter_override:
            return config_override, filter_override, None

        from strategy.backtest.config import get_config as _get_config

        results_path = self.output_dir / "heatmap_results.json"
        try:
            strategy_config = _get_config(
                mesa_index, results_path, self.reg.mesa_dict_to_config_fn
            )
            cfg, filt = strategy_config.get_configs()
            return cfg, filt, strategy_config
        except (FileNotFoundError, ValueError) as exc:
            # No heatmap results or no Mesa regions — use default config/filter
            print(f"{exc} Using default parameters.")
            cfg = self.reg.config_cls()
            filt_kwargs = self.reg.default_filter_kwargs or {}
            filt = self.reg.filter_config_cls(**filt_kwargs)
            return cfg, filt, None

    def _create_bt_config(self, data: pd.DataFrame) -> BacktestConfig:
        return BacktestConfig(
            symbol=self.symbol,
            interval=self.reg.default_interval,
            start_date=data.index[0].to_pydatetime(),
            end_date=data.index[-1].to_pydatetime(),
            initial_capital=10000.0,
            exchange=self.profile.ccxt_id,
            leverage=self.leverage,
        )

    def _make_signal_fn(self, base_config, base_filter, extra_params=None, funding_rates=None):
        """Return a signal function suitable for GridSearchOptimizer / WalkForwardAnalyzer.

        Note: funding_rates injected here is used for *signal generation* (e.g. funding_rate
        strategy uses it to decide entry timing). Cost-model funding rates are passed
        separately to GridSearchOptimizer / WalkForwardAnalyzer.
        """
        gen = self.reg.signal_generator_cls(base_config, base_filter)
        # Inject funding rate data if the generator supports it
        if hasattr(gen, "funding_rates") and funding_rates is not None:
            gen.funding_rates = funding_rates

        def signal_fn(df: pd.DataFrame, params: Dict) -> np.ndarray:
            merged = {**(extra_params or {}), **params}
            return _apply_signal_delay(gen.generate(df, merged))

        return signal_fn

    def _split_params(self, params: Optional[Dict]):
        """Split mixed params dict into (config_kw, filter_kw)."""
        if self.reg.split_params_fn:
            return self.reg.split_params_fn(params)
        # Default: all params go to config, none to filter
        return (params or {}), {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_single(
        self,
        data: pd.DataFrame,
        mesa_index: int = 0,
        period: str = None,
        funding_rates: Optional[pd.DataFrame] = None,
        config_override=None,
        filter_override=None,
    ) -> Dict[str, Any]:
        """Run backtest with a single configuration."""
        cfg, filt, strategy_config = self._get_configs(
            mesa_index, config_override, filter_override
        )

        if len(data) == 0:
            print("No data in specified date range")
            return {}

        bt_config = self._create_bt_config(data)
        has_funding_data = funding_rates is not None and not funding_rates.empty
        if not has_funding_data and self.reg.name == "funding_rate":
            print("WARNING: No funding rate data. Funding costs will not be modeled.")
        cost_config = self.profile.cost_config(use_funding_rate=has_funding_data)

        gen = self.reg.signal_generator_cls(cfg, filt)
        # Inject funding rate data if the generator supports it
        if hasattr(gen, "funding_rates") and funding_rates is not None:
            gen.funding_rates = funding_rates
        signals = gen.generate(data)
        signals = _apply_signal_delay(signals)

        psp = _get_position_size_pct(cfg)
        bt = VectorizedBacktest(config=bt_config, cost_config=cost_config, position_size_pct=psp)
        result = bt.run(data=data, signals=signals, funding_rates=funding_rates)

        analyzer = PerformanceAnalyzer(
            equity_curve=result.equity_curve,
            trades=result.trades,
            initial_capital=bt_config.initial_capital,
        )
        metrics = analyzer.calculate_metrics()

        funding_paid = result.metrics.get("total_funding_paid", 0)
        config_name = strategy_config.name if strategy_config else "Custom"
        bh_return = _bh_return_pct(data, self.leverage)

        from nexustrader.backtest.analysis.performance import infer_periods_per_year
        ppy = infer_periods_per_year(result.equity_curve.index)
        sharpe_lo, sharpe_hi = _bootstrap_sharpe_ci(result.equity_curve, ppy)

        print(f"\n{'=' * 60}")
        print(f"BACKTEST RESULTS - {config_name}")
        print(f"{'=' * 60}")
        print(f"Exchange: {self.profile.name}")
        print(f"Leverage: {self.leverage}x")
        print(f"Period: {data.index[0].date()} to {data.index[-1].date()}")
        print(f"Net Profit:    ${metrics['net_profit']:+,.2f}")
        print(f"Total Return:  {metrics['total_return_pct']:+.2f}%  (B&H: {bh_return:+.2f}%)")
        print(f"Gross Profit:  ${metrics['gross_profit']:,.2f}   Gross Loss: ${metrics['gross_loss']:,.2f}")
        print(f"Commission:    ${metrics['commission_paid']:,.2f}")
        if metrics.get('open_pl', 0) != 0:
            print(f"Open PL:       ${metrics['open_pl']:+,.2f}")
        print(f"Max Drawdown:  {metrics['max_drawdown_pct']:.2f}%")
        sharpe_str = f"{metrics['sharpe_ratio']:.2f}"
        if sharpe_lo is not None:
            sharpe_str += f"  [95% CI: {sharpe_lo:.2f}, {sharpe_hi:.2f}]"
        print(f"Sharpe Ratio:  {sharpe_str}")
        print(f"Sortino Ratio: {metrics['sortino_ratio']:.2f}")
        print(f"Calmar Ratio:  {metrics['calmar_ratio']:.2f}")
        total_trades = metrics['total_trades']
        trade_warning = "  ⚠ Low trade count" if total_trades < 10 else ""
        print(f"Total Trades:  {total_trades}{trade_warning}")
        print(f"Win Rate:      {metrics['win_rate_pct']:.1f}%")
        print(f"Avg Trade:     ${metrics['avg_trade_dollar']:+,.2f}  ({metrics['avg_trade_pct']:+.2f}%)")
        print(f"Avg Win:       ${metrics['avg_win']:,.2f}   Avg Loss: ${metrics['avg_loss']:,.2f}")
        print(f"Profit Factor: {metrics['profit_factor']:.2f}")
        if metrics.get('avg_bars_held', 0) > 0:
            print(f"Avg Bars Held: {metrics['avg_bars_held']:.1f}  (win: {metrics['avg_bars_held_winning']:.1f}, loss: {metrics['avg_bars_held_losing']:.1f})")
        if funding_paid != 0:
            print(f"Funding Paid:  ${funding_paid:.2f}")
        print(f"{'=' * 60}")

        result_dict = {
            "mesa_index": mesa_index,
            "config_name": config_name,
            "period": period,
            "start_date": str(data.index[0].date()),
            "end_date": str(data.index[-1].date()),
            "run_time": datetime.now().isoformat(),
            "total_return_pct": round(metrics["total_return_pct"], 2),
            "bh_return_pct": round(bh_return, 2),
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
        if sharpe_lo is not None:
            result_dict["sharpe_ci_lo"] = round(sharpe_lo, 2)
            result_dict["sharpe_ci_hi"] = round(sharpe_hi, 2)
        return result_dict

    def run_grid_search(
        self,
        data: pd.DataFrame,
        train_ratio: float = 0.8,
        period: str = None,
        funding_rates: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Run grid search optimization."""
        train_idx = int(len(data) * train_ratio)
        train_data = data.iloc[:train_idx]

        print(f"\n{'=' * 60}")
        print("GRID SEARCH OPTIMIZATION")
        print(f"{'=' * 60}")
        print(
            f"Training period: {train_data.index[0].date()} to {train_data.index[-1].date()}"
        )
        print(f"Training bars: {len(train_data)}")

        bt_config = self._create_bt_config(train_data)
        has_funding_data = funding_rates is not None and not funding_rates.empty
        cost_config = self.profile.cost_config(use_funding_rate=has_funding_data)

        # Slice funding rates to training period
        train_funding = None
        if has_funding_data:
            ts = funding_rates.index
            train_funding = funding_rates[
                (ts >= train_data.index[0]) & (ts <= train_data.index[-1])
            ]

        base_config = self.reg.config_cls()
        base_filter = self.reg.filter_config_cls(**self.reg.default_filter_kwargs)
        signal_fn = self._make_signal_fn(base_config, base_filter, funding_rates=funding_rates)

        psp = _get_position_size_pct(base_config)
        optimizer = GridSearchOptimizer(
            data=train_data,
            config=bt_config,
            signal_generator=signal_fn,
            cost_config=cost_config,
            position_size_pct=psp,
            n_jobs=self.n_jobs,
            funding_rates=train_funding,
        )

        grid = ParameterGrid(**self.reg.default_grid)

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
        print(f"{'=' * 60}")

        return {
            "results": results,
            "best_params": best_params,
            "best_metrics": best_metrics,
            "train_data": train_data,
            "period": period,
        }

    def run_walk_forward(
        self,
        data: pd.DataFrame,
        params: Dict = None,
        funding_rates: Optional[pd.DataFrame] = None,
    ) -> Dict[str, Any]:
        """Run walk-forward validation."""
        print(f"\n{'=' * 60}")
        print("WALK-FORWARD VALIDATION")
        print(f"{'=' * 60}")

        bt_config = self._create_bt_config(data)
        has_funding_data = funding_rates is not None and not funding_rates.empty
        cost_config = self.profile.cost_config(use_funding_rate=has_funding_data)

        cfg_kw, filt_kw = self._split_params(params)
        base_config = self.reg.config_cls(**cfg_kw)
        base_filter = (
            self.reg.filter_config_cls(**filt_kw)
            if filt_kw
            else self.reg.filter_config_cls(**self.reg.default_filter_kwargs)
        )
        signal_fn = self._make_signal_fn(base_config, base_filter, params, funding_rates=funding_rates)

        bpd = _bars_per_day(self.reg.default_interval)
        train_periods = bpd * 90   # 90 calendar days
        test_periods = bpd * 30    # 30 calendar days

        if len(data) < train_periods + test_periods:
            print(
                f"Insufficient data for walk-forward: need {train_periods + test_periods} bars, "
                f"have {len(data)}. Reducing window sizes."
            )
            train_periods = min(train_periods, len(data) * 2 // 3)
            test_periods = min(test_periods, len(data) // 3)

        psp = _get_position_size_pct(base_config)
        wf_analyzer = WalkForwardAnalyzer(
            data=data,
            config=bt_config,
            signal_generator=signal_fn,
            train_periods=train_periods,
            test_periods=test_periods,
            window_type=WindowType.ROLLING,
            cost_config=cost_config,
            position_size_pct=psp,
            funding_rates=funding_rates if has_funding_data else None,
        )

        if params:
            # Called from three-stage (stability check with fixed params)
            param_grid = ParameterGrid(dummy=[1])
        else:
            # Standalone --walk-forward: re-optimize in each window
            print(f"Note: true WFO with {len(ParameterGrid(**self.reg.default_grid))} param combinations per window.")
            param_grid = ParameterGrid(**self.reg.default_grid)
        results = wf_analyzer.run(param_grid)
        summary = wf_analyzer.get_summary(results)

        wc = summary.get("windows_count", 0)
        print(f"Windows: {wc}")
        if wc > 0:
            print(f"Avg Train Return: {summary['avg_train_return']:.2f}%")
            print(f"Avg Test Return: {summary['avg_test_return']:.2f}%")
            print(f"Robustness Ratio: {summary['robustness_ratio']:.2f}")
            print(f"Positive Test Windows: {summary['positive_test_windows']}/{wc}")
            print(f"Total Test Return: {summary['total_test_return']:.2f}%")
        else:
            print("No walk-forward windows could be generated.")
            summary.setdefault("avg_train_return", 0)
            summary.setdefault("avg_test_return", 0)
            summary.setdefault("robustness_ratio", 0)
            summary.setdefault("positive_test_windows", 0)
            summary.setdefault("total_test_return", 0)
        print(f"{'=' * 60}")

        return {
            "results": results,
            "summary": summary,
        }

    def run_regime_analysis(
        self,
        data: pd.DataFrame,
        result,
    ) -> Dict[str, Any]:
        """Run market regime analysis."""
        print(f"\n{'=' * 60}")
        print("MARKET REGIME ANALYSIS")
        print(f"{'=' * 60}")

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
            print(
                f"  {regime}: {metrics['return_pct']:+.2f}% return ({metrics['count']} bars)"
            )

        print(f"{'=' * 60}")

        return {
            "regimes": regimes,
            "performance": performance,
            "summary": regime_summary,
        }

    def run_three_stage_test(
        self,
        data: pd.DataFrame,
        funding_rates: Optional[pd.DataFrame] = None,
        period: str = DEFAULT_PERIOD,
        export_config_flag: bool = False,
    ) -> Dict[str, Any]:
        """Run complete three-stage backtest validation."""
        print("\n" + "=" * 80)
        print(f"Three-Stage Backtest Validation ({self.reg.display_name})")
        print("=" * 80)
        print(f"Exchange: {self.profile.name}")
        print(f"Data period: {data.index[0].date()} to {data.index[-1].date()}")
        print(f"Total bars: {len(data)}")
        print(f"Period: {period}")
        print("=" * 80)

        train_ratio = THREE_STAGE_CONFIG["train_ratio"]
        split_idx = int(len(data) * train_ratio)
        train_data = data.iloc[:split_idx]
        holdout_data = data.iloc[split_idx:]

        print("\nData split:")
        print(
            f"  Train (80%): {train_data.index[0].date()} to {train_data.index[-1].date()} ({len(train_data)} bars)"
        )
        print(
            f"  Holdout (20%): {holdout_data.index[0].date()} to {holdout_data.index[-1].date()} ({len(holdout_data)} bars)"
        )

        results = {}

        # Stage 1: In-sample optimization
        print("\n" + "=" * 80)
        print(f"Stage 1: {THREE_STAGE_CONFIG['stage1_name']}")
        print("=" * 80)

        # Slice funding rates for train period
        train_funding = None
        holdout_funding = None
        if funding_rates is not None and not funding_rates.empty:
            ts = funding_rates.index
            train_funding = funding_rates[
                (ts >= train_data.index[0]) & (ts <= train_data.index[-1])
            ]
            holdout_funding = funding_rates[
                (ts >= holdout_data.index[0]) & (ts <= holdout_data.index[-1])
            ]

        opt_result = self.run_grid_search(
            train_data, train_ratio=1.0, period=period, funding_rates=train_funding
        )
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

        print("\nStage 1 Results:")
        print(f"  Best params: {best_params}")
        print(f"  In-sample return: {results['stage1']['in_sample_return']:+.2f}%")
        print(f"  In-sample Sharpe: {results['stage1']['in_sample_sharpe']:.2f}")

        # Stage 2: Walk-forward validation
        print("\n" + "=" * 80)
        print(f"Stage 2: {THREE_STAGE_CONFIG['stage2_name']}")
        print("=" * 80)

        wf_result = self.run_walk_forward(train_data, best_params, funding_rates=train_funding)
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

        robustness_pass = wf_summary["robustness_ratio"] >= 0.5
        positive_pct = wf_summary["positive_test_windows"] / max(
            wf_summary["windows_count"], 1
        )
        consistency_pass = positive_pct >= 0.5

        print("\nStage 2 Results:")
        print(f"  Windows: {wf_summary['windows_count']}")
        print(
            f"  Robustness ratio: {wf_summary['robustness_ratio']:.2f} "
            f"{'PASS' if robustness_pass else 'FAIL'} (>= 0.5)"
        )
        print(
            f"  Positive windows: {wf_summary['positive_test_windows']}/{wf_summary['windows_count']} "
            f"({positive_pct:.0%}) {'PASS' if consistency_pass else 'FAIL'} (>= 50%)"
        )

        # Stage 3: Holdout test + Regime analysis
        print("\n" + "=" * 80)
        print(f"Stage 3: {THREE_STAGE_CONFIG['stage3_name']}")
        print("=" * 80)

        has_holdout_funding = holdout_funding is not None and not holdout_funding.empty
        bt_config = self._create_bt_config(holdout_data)
        cost_config = self.profile.cost_config(use_funding_rate=has_holdout_funding)

        cfg_kw, filt_kw = self._split_params(best_params)
        base_config = self.reg.config_cls(**cfg_kw)
        base_filter = (
            self.reg.filter_config_cls(**filt_kw)
            if filt_kw
            else self.reg.filter_config_cls(**self.reg.default_filter_kwargs)
        )
        gen = self.reg.signal_generator_cls(base_config, base_filter)
        if hasattr(gen, "funding_rates") and holdout_funding is not None:
            gen.funding_rates = holdout_funding
        signals = gen.generate(holdout_data, best_params)
        signals = _apply_signal_delay(signals)

        psp = _get_position_size_pct(base_config)
        bt = VectorizedBacktest(config=bt_config, cost_config=cost_config, position_size_pct=psp)
        holdout_result = bt.run(
            data=holdout_data, signals=signals, funding_rates=holdout_funding
        )

        analyzer = PerformanceAnalyzer(
            equity_curve=holdout_result.equity_curve,
            trades=holdout_result.trades,
            initial_capital=bt_config.initial_capital,
        )
        holdout_metrics = analyzer.calculate_metrics()

        # Monte Carlo: bootstrap 95% CI for holdout Sharpe
        from nexustrader.backtest.analysis.performance import infer_periods_per_year
        ppy = infer_periods_per_year(holdout_result.equity_curve.index)
        sharpe_lo, sharpe_hi = _bootstrap_sharpe_ci(holdout_result.equity_curve, ppy)

        regime_result = self.run_regime_analysis(holdout_data, holdout_result)
        bh_holdout = _bh_return_pct(holdout_data, self.leverage)
        bh_full = _bh_return_pct(data, self.leverage)

        results["stage3"] = {
            "name": THREE_STAGE_CONFIG["stage3_name"],
            "holdout_return": holdout_metrics["total_return_pct"],
            "bh_holdout_return": round(bh_holdout, 2),
            "holdout_sharpe": holdout_metrics["sharpe_ratio"],
            "holdout_sharpe_ci_lo": round(sharpe_lo, 2) if sharpe_lo is not None else None,
            "holdout_sharpe_ci_hi": round(sharpe_hi, 2) if sharpe_hi is not None else None,
            "holdout_drawdown": holdout_metrics["max_drawdown_pct"],
            "holdout_trades": holdout_metrics["total_trades"],
            "holdout_win_rate": holdout_metrics["win_rate_pct"],
            "regime_summary": regime_result["summary"],
            "regime_performance": {
                k: v["return_pct"] for k, v in regime_result["performance"].items()
            },
        }

        in_sample_return = results["stage1"]["in_sample_return"]
        holdout_return = results["stage3"]["holdout_return"]
        if in_sample_return > 0:
            degradation = (
                1 - (holdout_return / in_sample_return) if holdout_return >= 0 else 1.0
            )
        else:
            degradation = 0 if holdout_return <= in_sample_return else 1.0

        degradation_pass = degradation <= 0.5

        print("\nStage 3 Results (holdout):")
        print(f"  Holdout return: {holdout_return:+.2f}%  (B&H: {bh_holdout:+.2f}%)")
        holdout_sharpe_str = f"{holdout_metrics['sharpe_ratio']:.2f}"
        if sharpe_lo is not None:
            holdout_sharpe_str += f"  [95% CI: {sharpe_lo:.2f}, {sharpe_hi:.2f}]"
        print(f"  Holdout Sharpe: {holdout_sharpe_str}")
        print(f"  Max drawdown: {holdout_metrics['max_drawdown_pct']:.2f}%")
        holdout_trades = holdout_metrics['total_trades']
        trade_warn = "  (low trade count)" if holdout_trades < 10 else ""
        print(f"  Trades: {holdout_trades}{trade_warn}")
        print(
            f"  Degradation: {degradation:.0%} {'PASS' if degradation_pass else 'FAIL'} (<= 50%)"
        )
        print(f"\n  Full period B&H: {bh_full:+.2f}%")

        # Summary
        print("\n" + "=" * 80)
        print("Three-Stage Test Summary")
        print("=" * 80)

        in_sample_trades = results["stage1"]["in_sample_trades"]
        min_trades_pass = in_sample_trades >= 10
        stage1_pass = results["stage1"]["in_sample_sharpe"] >= 1.0 and min_trades_pass
        stage2_pass = robustness_pass and consistency_pass
        stage3_pass = degradation_pass and holdout_metrics["sharpe_ratio"] >= 0.5

        all_pass = stage1_pass and stage2_pass and stage3_pass

        print(f"\n{'Stage':<25} {'Status':<10} {'Key Metric'}")
        print("-" * 60)
        s1_detail = f"Sharpe={results['stage1']['in_sample_sharpe']:.2f}"
        if not min_trades_pass:
            s1_detail += f", Trades={in_sample_trades} (< 10)"
        print(
            f"{'Stage 1: Optimization':<25} {'PASS' if stage1_pass else 'FAIL':<10} "
            f"{s1_detail}"
        )
        print(
            f"{'Stage 2: Walk-Forward':<25} {'PASS' if stage2_pass else 'FAIL':<10} "
            f"Robustness={wf_summary['robustness_ratio']:.2f}"
        )
        print(
            f"{'Stage 3: Holdout':<25} {'PASS' if stage3_pass else 'FAIL':<10} "
            f"Sharpe={holdout_metrics['sharpe_ratio']:.2f}, Degradation={degradation:.0%}"
        )
        print("-" * 60)
        print(
            f"{'Overall':<25} {'PASS - Strategy viable' if all_pass else 'FAIL - Needs improvement'}"
        )

        results["summary"] = {
            "stage1_pass": stage1_pass,
            "stage2_pass": stage2_pass,
            "stage3_pass": stage3_pass,
            "all_pass": all_pass,
            "best_params": best_params,
            "in_sample_sharpe": results["stage1"]["in_sample_sharpe"],
            "holdout_sharpe": holdout_metrics["sharpe_ratio"],
            "holdout_sharpe_ci_lo": round(sharpe_lo, 2) if sharpe_lo is not None else None,
            "holdout_sharpe_ci_hi": round(sharpe_hi, 2) if sharpe_hi is not None else None,
            "robustness_ratio": wf_summary["robustness_ratio"],
            "degradation": degradation,
            "bh_full_return": round(bh_full, 2),
        }

        if export_config_flag and self.reg.export_config_fn:
            config_code = self.reg.export_config_fn(
                best_params, holdout_metrics, period, self.profile
            )
            print("\n" + "=" * 60)
            print("EXPORT CONFIG FOR PAPER TRADING")
            print("=" * 60)
            print(config_code)

            export_file = self.output_dir / "optimized_config.py"
            with open(export_file, "w") as f:
                f.write(config_code)
            print(f"\nConfig saved to {export_file}")

        return results

    def run_heatmap(
        self,
        data: pd.DataFrame,
        funding_rates: Optional[pd.DataFrame] = None,
        period: str = "1y",
        resolution: int = 15,
        third_param: Optional[str] = None,
        all_regimes: bool = False,
    ) -> None:
        """Run heatmap parameter scan."""
        from strategy.backtest.heatmap import run_heatmap_scan

        hc = self.reg.heatmap_config
        fixed_params = dict(hc.fixed_params)

        has_funding_data = funding_rates is not None and not funding_rates.empty
        if not has_funding_data and self.reg.name == "funding_rate":
            print("WARNING: No funding rate data. Funding costs will not be modeled.")
        cost_config = self.profile.cost_config(use_funding_rate=has_funding_data)

        run_heatmap_scan(
            data=data,
            signal_generator_cls=self.reg.signal_generator_cls,
            config_cls=self.reg.config_cls,
            filter_config_cls=self.reg.filter_config_cls,
            funding_rates=funding_rates,
            period=period,
            resolution=resolution,
            third_param=third_param,
            third_param_choices=hc.third_param_choices,
            all_regimes=all_regimes,
            output_dir=self.output_dir,
            strategy_name=self.reg.display_name,
            x_param_name=hc.x_param_name,
            y_param_name=hc.y_param_name,
            x_range=hc.x_range,
            y_range=hc.y_range,
            x_label=hc.x_label,
            y_label=hc.y_label,
            fixed_params=fixed_params,
            filter_config_factory=hc.filter_config_factory,
            symbol=self.symbol,
            cost_config=cost_config,
            leverage=self.leverage,
            n_jobs=self.n_jobs,
        )

    def generate_report(self, result, output_path: Path = None, data: pd.DataFrame = None):
        """Generate HTML report with optional B&H comparison line."""
        if output_path is None:
            output_path = self._report_file

        bh_equity = None
        if data is not None and len(data) >= 2:
            initial = result.config.initial_capital
            bh_equity = (
                data["close"] / data["close"].iloc[0] * initial * self.leverage
            )

        print(f"\nGenerating report to {output_path}...")
        generator = ReportGenerator(result, bh_equity_curve=bh_equity)
        generator.save(output_path)
        print(f"Report saved to {output_path}")

    def load_results(self) -> Dict[str, Any]:
        """Load saved backtest results."""
        return _load_results(self._results_file)

    def save_results(self, results: Dict[str, Any]) -> None:
        """Save backtest results."""
        _save_results(results, self._results_file)

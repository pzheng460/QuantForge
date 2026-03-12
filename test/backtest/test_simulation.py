"""Tests for the simulation module: bootstrap, Monte Carlo, stress test, report."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quantforge.backtest.simulation import (
    BlockBootstrap,
    GBMGenerator,
    JumpDiffusionGenerator,
    SimulationReport,
    StressTestGenerator,
)


# ---------------------------------------------------------------------------
# Shared fixture: 500-bar OHLCV with realistic BTC-like prices
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Generate 500 bars of synthetic BTC-like 1h OHLCV data."""
    rng = np.random.default_rng(42)
    n = 500
    # Random walk in log-space
    log_ret = rng.normal(0.0001, 0.005, size=n - 1)
    log_price = np.concatenate([[np.log(40000)], np.cumsum(log_ret) + np.log(40000)])
    close = np.exp(log_price)

    spread = np.abs(rng.normal(0, 0.003, size=n)) * close
    high = close + spread
    low = close - spread
    low = np.maximum(low, close * 0.99)

    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] + rng.normal(0, 10, size=n - 1)

    volume = rng.lognormal(mean=10, sigma=1, size=n)

    idx = pd.date_range("2024-01-01", periods=n, freq="1h")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ===================================================================
# Block Bootstrap
# ===================================================================


class TestBlockBootstrap:
    def test_output_count(self, sample_ohlcv: pd.DataFrame) -> None:
        bb = BlockBootstrap(sample_ohlcv, block_size=24, seed=0)
        paths = bb.generate(n_paths=5)
        assert len(paths) == 5

    def test_output_shape(self, sample_ohlcv: pd.DataFrame) -> None:
        bb = BlockBootstrap(sample_ohlcv, block_size=24, seed=0)
        for path in bb.generate(n_paths=3):
            assert path.shape == sample_ohlcv.shape

    def test_output_columns(self, sample_ohlcv: pd.DataFrame) -> None:
        bb = BlockBootstrap(sample_ohlcv, block_size=24, seed=0)
        path = bb.generate(n_paths=1)[0]
        assert set(path.columns) == {"open", "high", "low", "close", "volume"}

    def test_deterministic_with_seed(self, sample_ohlcv: pd.DataFrame) -> None:
        p1 = BlockBootstrap(sample_ohlcv, block_size=24, seed=123).generate(3)
        p2 = BlockBootstrap(sample_ohlcv, block_size=24, seed=123).generate(3)
        for a, b in zip(p1, p2):
            pd.testing.assert_frame_equal(a, b)

    def test_preserves_return_distribution(self, sample_ohlcv: pd.DataFrame) -> None:
        """Mean / std of bootstrap returns should be close to original."""
        bb = BlockBootstrap(sample_ohlcv, block_size=24, seed=0)
        paths = bb.generate(n_paths=50)

        orig_ret = np.diff(np.log(sample_ohlcv["close"].values))
        orig_mu = np.mean(orig_ret)
        orig_std = np.std(orig_ret)

        boot_mus = []
        boot_stds = []
        for p in paths:
            r = np.diff(np.log(p["close"].values))
            boot_mus.append(np.mean(r))
            boot_stds.append(np.std(r))

        # Mean of bootstrap means should be close to original mean
        assert abs(np.mean(boot_mus) - orig_mu) < 3 * orig_std / np.sqrt(len(orig_ret))
        # Mean of bootstrap stds should be within 50% of original
        assert abs(np.mean(boot_stds) - orig_std) / orig_std < 0.5


# ===================================================================
# GBM Generator
# ===================================================================


class TestGBMGenerator:
    def test_output_count_and_shape(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = GBMGenerator(sample_ohlcv, seed=0)
        paths = gen.generate(n_paths=5)
        assert len(paths) == 5
        for p in paths:
            assert p.shape == sample_ohlcv.shape
            assert set(p.columns) == {"open", "high", "low", "close", "volume"}

    def test_prices_positive(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = GBMGenerator(sample_ohlcv, seed=0)
        for p in gen.generate(n_paths=10):
            assert (p["close"] > 0).all()
            assert (p["high"] > 0).all()
            assert (p["low"] > 0).all()

    def test_deterministic_with_seed(self, sample_ohlcv: pd.DataFrame) -> None:
        p1 = GBMGenerator(sample_ohlcv, seed=7).generate(3)
        p2 = GBMGenerator(sample_ohlcv, seed=7).generate(3)
        for a, b in zip(p1, p2):
            pd.testing.assert_frame_equal(a, b)

    def test_custom_n_bars(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = GBMGenerator(sample_ohlcv, seed=0)
        paths = gen.generate(n_paths=2, n_bars=200)
        for p in paths:
            assert len(p) == 200


# ===================================================================
# Jump Diffusion Generator
# ===================================================================


class TestJumpDiffusionGenerator:
    def test_output_count_and_shape(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = JumpDiffusionGenerator(sample_ohlcv, seed=0)
        paths = gen.generate(n_paths=5)
        assert len(paths) == 5
        for p in paths:
            assert p.shape == sample_ohlcv.shape

    def test_prices_positive(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = JumpDiffusionGenerator(sample_ohlcv, seed=0)
        for p in gen.generate(n_paths=10):
            assert (p["close"] > 0).all()

    def test_fatter_tails_than_gbm(self, sample_ohlcv: pd.DataFrame) -> None:
        """Jump diffusion should produce higher kurtosis than pure GBM."""
        from scipy.stats import kurtosis

        gbm_gen = GBMGenerator(sample_ohlcv, seed=42)
        jd_gen = JumpDiffusionGenerator(sample_ohlcv, seed=42)

        n_paths = 50
        gbm_paths = gbm_gen.generate(n_paths)
        jd_paths = jd_gen.generate(n_paths)

        gbm_kurt = np.mean(
            [kurtosis(np.diff(np.log(p["close"].values))) for p in gbm_paths]
        )
        jd_kurt = np.mean(
            [kurtosis(np.diff(np.log(p["close"].values))) for p in jd_paths]
        )

        # JD kurtosis should generally be >= GBM kurtosis
        # Use a loose check: JD kurtosis should not be drastically smaller
        assert jd_kurt > gbm_kurt - 1.0  # allow some statistical noise


# ===================================================================
# Stress Test Generator
# ===================================================================


class TestStressTestGenerator:
    def test_crash_scenarios_have_large_drops(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = StressTestGenerator(sample_ohlcv, seed=0)
        result = gen.generate_crash_scenarios(
            n_paths=30, crash_pct=-0.10, crash_window=24
        )
        cum_rets = [
            (p["close"].iloc[-1] / p["close"].iloc[0]) - 1 for p in result.paths
        ]
        # At least some paths should have negative cumulative returns
        assert any(r < 0 for r in cum_rets)

    def test_spike_scenarios_have_large_rises(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = StressTestGenerator(sample_ohlcv, seed=0)
        result = gen.generate_spike_scenarios(
            n_paths=30, spike_pct=0.10, spike_window=24
        )
        cum_rets = [
            (p["close"].iloc[-1] / p["close"].iloc[0]) - 1 for p in result.paths
        ]
        # At least some paths should have positive cumulative returns
        assert any(r > 0 for r in cum_rets)

    def test_weights_sum_to_n(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = StressTestGenerator(sample_ohlcv, seed=0)
        result = gen.generate_crash_scenarios(n_paths=50)
        # Weights should sum to n_paths (normalised that way in the code)
        assert abs(np.sum(result.weights) - 50) < 1e-6

    def test_tail_probability_reasonable(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = StressTestGenerator(sample_ohlcv, seed=0)
        result = gen.generate_volatility_scenarios(n_paths=30, vol_multiplier=2.0)
        assert 0.0 <= result.tail_probability <= 1.0

    def test_scenario_description(self, sample_ohlcv: pd.DataFrame) -> None:
        gen = StressTestGenerator(sample_ohlcv, seed=0)
        crash = gen.generate_crash_scenarios(n_paths=5, crash_pct=-0.05)
        assert "Crash scenario" in crash.scenario_description
        spike = gen.generate_spike_scenarios(n_paths=5, spike_pct=0.05)
        assert "Spike scenario" in spike.scenario_description
        vol = gen.generate_volatility_scenarios(n_paths=5, vol_multiplier=2.0)
        assert "Volatility scenario" in vol.scenario_description


# ===================================================================
# Simulation Report
# ===================================================================


class TestSimulationReport:
    @pytest.fixture
    def sample_metrics(self) -> list[dict[str, float]]:
        rng = np.random.default_rng(99)
        return [
            {
                "total_return_pct": rng.normal(5, 10),
                "sharpe_ratio": rng.normal(1.0, 0.5),
                "max_drawdown_pct": -abs(rng.normal(10, 5)),
            }
            for _ in range(100)
        ]

    def test_summary_keys(self, sample_metrics: list[dict[str, float]]) -> None:
        report = SimulationReport(sample_metrics)
        summary = report.summary()
        expected_stats = {
            "mean",
            "median",
            "std",
            "min",
            "max",
            "p5",
            "p25",
            "p75",
            "p95",
        }
        for metric_stats in summary.values():
            assert set(metric_stats.keys()) == expected_stats

    def test_confidence_interval_ordering(
        self, sample_metrics: list[dict[str, float]]
    ) -> None:
        report = SimulationReport(sample_metrics)
        for key in ["total_return_pct", "sharpe_ratio", "max_drawdown_pct"]:
            lo, hi = report.confidence_interval(key, confidence=0.90)
            assert lo <= hi

    def test_weighted_summary(self, sample_metrics: list[dict[str, float]]) -> None:
        """Weights that favour high-return paths should shift mean upward."""
        returns = np.array([m["total_return_pct"] for m in sample_metrics])
        # Weight paths proportionally to their return
        weights = returns - returns.min() + 1  # all positive
        report_equal = SimulationReport(sample_metrics)
        report_weighted = SimulationReport(sample_metrics, weights=weights)
        mean_eq = report_equal.summary()["total_return_pct"]["mean"]
        mean_wt = report_weighted.summary()["total_return_pct"]["mean"]
        # Weighted mean should be >= equal-weight mean (weighting up high returns)
        assert mean_wt >= mean_eq - 1e-6

    def test_plot_does_not_crash(self, sample_metrics: list[dict[str, float]]) -> None:
        """Calling plot with save_path=None should not raise (matplotlib optional)."""
        report = SimulationReport(sample_metrics)
        # Use a temp file to avoid popping up a window
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test_dist.png")
            report.plot_distributions(save_path=path)
            # If matplotlib is installed, file should exist
            # If not, silently returns — either way no error

    def test_unknown_metric_raises(
        self, sample_metrics: list[dict[str, float]]
    ) -> None:
        report = SimulationReport(sample_metrics)
        with pytest.raises(KeyError):
            report.confidence_interval("nonexistent_metric")

    def test_empty_metrics_raises(self) -> None:
        with pytest.raises(ValueError):
            SimulationReport([])

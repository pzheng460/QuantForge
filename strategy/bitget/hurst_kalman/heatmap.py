"""
Hurst-Kalman Heatmap — thin wrapper over unified heatmap module.

Re-exports all common classes and provides a backward-compatible
``run_heatmap_scan()`` that fills in HK-specific defaults
(axis names, ranges, labels, fixed_params, output directory).
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from strategy.backtest.heatmap import (  # noqa: F401
    FREQUENCY_BANDS,
    CellResult,
    ConfigExporter,
    FrequencyAnalyzer,
    HeatmapReportGenerator,
    HeatmapResults,
    HeatmapScanner,
    MesaDetector,
    MesaRegion,
)
from strategy.backtest.heatmap import run_heatmap_scan as _generic_run

_DIR = Path(__file__).resolve().parent

# HK-specific third-param choices
_THIRD_PARAM_CHOICES = {
    "mean_reversion_threshold": [0.35, 0.40, 0.45],
    "kalman_R": [0.1, 0.2, 0.3],
}


def run_heatmap_scan(
    data: pd.DataFrame,
    signal_generator_cls,
    config_cls,
    filter_config_cls,
    funding_rates: Optional[pd.DataFrame] = None,
    period: str = "1y",
    resolution: int = 15,
    third_param: Optional[str] = None,
    all_regimes: bool = False,
) -> None:
    """Backward-compatible entry point for Hurst-Kalman heatmap scan."""
    only_mr = not all_regimes
    fixed_params = {
        "kalman_R": 0.2,
        "zscore_window": 60,
        "kalman_Q": 5e-05,
        "mean_reversion_threshold": 0.40,
        "trend_threshold": 0.60,
        "stop_loss_pct": 0.03,
        "zscore_stop": 4.0,
        "position_size_pct": 0.10,
        "daily_loss_limit": 0.03,
        "only_mean_reversion": only_mr,
    }

    if all_regimes:
        print("Mode: ALL REGIMES (mean-reversion + trending + random walk)")
    else:
        print("Mode: MEAN-REVERSION ONLY")

    _generic_run(
        data=data,
        signal_generator_cls=signal_generator_cls,
        config_cls=config_cls,
        filter_config_cls=filter_config_cls,
        funding_rates=funding_rates,
        period=period,
        resolution=resolution,
        third_param=third_param,
        third_param_choices=_THIRD_PARAM_CHOICES,
        all_regimes=all_regimes,
        output_dir=_DIR,
        strategy_name="Hurst-Kalman",
        x_param_name="zscore_entry",
        y_param_name="hurst_window",
        x_range=(1.5, 5.0),
        y_range=(20, 200),
        x_label="Z-Score Entry",
        y_label="Hurst Window",
        fixed_params=fixed_params,
        filter_config_factory=None,  # use default HK-style derivation
    )

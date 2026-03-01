"""
Heatmap Parameter Scan System (exchange-agnostic).

Moved from ``strategy/live/common/heatmap.py`` and parameterized to accept
``symbol``, ``cost_config``, ``interval``, and ``initial_capital`` so the
BacktestRunner can thread exchange profile through it.

All original classes and the ``run_heatmap_scan()`` entry point are preserved.
"""

import dataclasses as _dc
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from nexustrader.backtest import (
    BacktestConfig,
    CostConfig,
    PerformanceAnalyzer,
    VectorizedBacktest,
)
from nexustrader.constants import KlineInterval

def _apply_signal_delay(signals: np.ndarray) -> np.ndarray:
    """Shift signals right by 1 bar: signal from bar i executes at bar i+1."""
    delayed = np.empty_like(signals)
    delayed[0] = 0
    delayed[1:] = signals[:-1]
    return delayed


# ---------------------------------------------------------------------------
# Frequency bands (trades per year)
# ---------------------------------------------------------------------------
FREQUENCY_BANDS = {
    "Daily (>250/yr)": (250, float("inf")),
    "Weekly (50-250/yr)": (50, 250),
    "Bi-Weekly (25-50/yr)": (25, 50),
    "Monthly (12-25/yr)": (12, 25),
    "Quarterly (4-12/yr)": (4, 12),
    "Yearly (<4/yr)": (0, 4),
}


# ---------------------------------------------------------------------------
# Data structures  (generic x / y naming)
# ---------------------------------------------------------------------------
@dataclass
class CellResult:
    """Result for a single (x_value, y_value) grid cell."""

    x_value: float
    y_value: float
    sharpe_ratio: float
    annualized_return_pct: float
    max_drawdown_pct: float
    win_rate_pct: float
    total_trades: int
    annualized_trades: float
    profit_factor: float
    total_return_pct: float


@dataclass
class MesaRegion:
    """A stable profitable plateau detected in the heatmap."""

    index: int
    x_range: Tuple[float, float]
    y_range: Tuple[float, float]
    center_x: float
    center_y: float
    avg_sharpe: float
    stability: float  # 1/(1+std(sharpe))
    avg_return_pct: float
    avg_max_dd_pct: float
    avg_trades_yr: float
    area: int  # number of cells
    frequency_label: str
    # Third-param info (which panel)
    third_param_name: Optional[str] = None
    third_param_value: Optional[float] = None
    panel_index: int = 0
    # Full config values for export
    extra_params: Dict[str, float] = field(default_factory=dict)


@dataclass
class HeatmapResults:
    """Full results from a heatmap scan."""

    x_values: List[float]
    y_values: List[float]
    panels: List[Dict[str, Any]]  # one per third_param value
    mesas: List[MesaRegion]
    frequency_summary: Dict[str, Any]
    scan_time_s: float
    period: str
    fixed_params: Dict[str, Any]


# ---------------------------------------------------------------------------
# HeatmapScanner
# ---------------------------------------------------------------------------
class HeatmapScanner:
    """Run parameter grid scan using the existing backtest engine."""

    def __init__(
        self,
        data: pd.DataFrame,
        signal_generator_cls,
        config_cls,
        filter_config_cls,
        funding_rates: Optional[pd.DataFrame] = None,
        x_param_name: str = "x",
        y_param_name: str = "y",
        filter_config_factory: Optional[Callable] = None,
        symbol: str = "BTC/USDT:USDT",
        cost_config: Optional[CostConfig] = None,
        interval: KlineInterval = KlineInterval.MINUTE_15,
        initial_capital: float = 10000.0,
        leverage: float = 1.0,
    ):
        self._data = data
        self._signal_generator_cls = signal_generator_cls
        self._config_cls = config_cls
        self._filter_config_cls = filter_config_cls
        self._funding_rates = funding_rates
        self._x_param_name = x_param_name
        self._y_param_name = y_param_name
        self._filter_config_factory = filter_config_factory
        self._symbol = symbol
        self._cost_config = cost_config or CostConfig(
            maker_fee=0.0002,
            taker_fee=0.0005,
            slippage_pct=0.0005,
            use_funding_rate=True,
        )
        self._interval = interval
        self._initial_capital = initial_capital
        self._leverage = leverage

    def scan(
        self,
        x_values: np.ndarray,
        y_values: np.ndarray,
        fixed_params: Dict[str, Any],
        third_param_name: Optional[str] = None,
        third_param_values: Optional[List[float]] = None,
    ) -> HeatmapResults:
        panels_data: List[Dict[str, Any]] = []
        all_cells: List[CellResult] = []

        if third_param_name and third_param_values:
            tp_values = third_param_values
        else:
            tp_values = [None]

        total = len(x_values) * len(y_values) * len(tp_values)
        done = 0
        t0 = time.time()

        for tp_val in tp_values:
            panel_label = (
                f"{third_param_name}={tp_val}" if tp_val is not None else "default"
            )
            grid = np.full((len(y_values), len(x_values)), np.nan, dtype=float)
            metrics_grid: Dict[str, np.ndarray] = {
                k: np.full((len(y_values), len(x_values)), np.nan)
                for k in [
                    "sharpe",
                    "ann_return",
                    "max_dd",
                    "win_rate",
                    "ann_trades",
                    "profit_factor",
                ]
            }
            cells: List[CellResult] = []

            for yi, yv in enumerate(y_values):
                for xi, xv in enumerate(x_values):
                    params = {
                        **fixed_params,
                        self._y_param_name: yv,
                        self._x_param_name: xv,
                    }
                    if tp_val is not None:
                        params[third_param_name] = tp_val

                    cell = self._run_single(params)
                    cells.append(cell)
                    all_cells.append(cell)

                    if cell.total_trades == 0:
                        pass  # leave as NaN
                    else:
                        grid[yi, xi] = cell.sharpe_ratio
                        metrics_grid["sharpe"][yi, xi] = cell.sharpe_ratio
                        metrics_grid["ann_return"][yi, xi] = cell.annualized_return_pct
                        metrics_grid["max_dd"][yi, xi] = cell.max_drawdown_pct
                        metrics_grid["win_rate"][yi, xi] = cell.win_rate_pct
                        metrics_grid["ann_trades"][yi, xi] = cell.annualized_trades
                        metrics_grid["profit_factor"][yi, xi] = cell.profit_factor

                    done += 1
                    if done % 25 == 0 or done == total:
                        elapsed = time.time() - t0
                        pct = done / total * 100
                        print(
                            f"\r  Scanning... [{done}/{total}] {pct:.0f}%  ({elapsed:.1f}s)",
                            end="",
                            flush=True,
                        )

            panels_data.append(
                {
                    "label": panel_label,
                    "third_param_name": third_param_name,
                    "third_param_value": tp_val,
                    "sharpe_grid": grid.tolist(),
                    "metrics": {k: v.tolist() for k, v in metrics_grid.items()},
                    "cells": cells,
                }
            )

        print()  # newline after progress
        scan_time = time.time() - t0

        return HeatmapResults(
            x_values=[float(x) for x in x_values],
            y_values=[float(y) for y in y_values],
            panels=panels_data,
            mesas=[],
            frequency_summary={},
            scan_time_s=round(scan_time, 1),
            period="",
            fixed_params=fixed_params,
        )

    # ------------------------------------------------------------------
    def _run_single(self, params: Dict[str, Any]) -> CellResult:
        yv = params[self._y_param_name]
        xv = params[self._x_param_name]

        # Convert y to int if it looks integral (e.g. hurst_window, slow_period)
        if isinstance(yv, float) and yv == int(yv):
            params[self._y_param_name] = int(yv)
        if isinstance(xv, float) and xv == int(xv):
            params[self._x_param_name] = int(xv)

        # Build strategy config — only pass fields that the config class accepts
        if _dc.is_dataclass(self._config_cls):
            valid_fields = {f.name for f in _dc.fields(self._config_cls)}
            config_params = {k: v for k, v in params.items() if k in valid_fields}
        else:
            config_params = {
                k: v for k, v in params.items() if k != "only_mean_reversion"
            }
        config = self._config_cls(**config_params)

        # Build filter config
        if self._filter_config_factory:
            filt = self._filter_config_factory(xv, yv, params)
        else:
            # Default HK-style derivation
            hw = int(yv)
            only_mr = params.get("only_mean_reversion", True)
            min_hold = max(2, hw // 12)
            cooldown = max(1, min_hold // 2)
            filt = self._filter_config_cls(
                min_holding_bars=min_hold,
                cooldown_bars=cooldown,
                signal_confirmation=1,
                only_mean_reversion=only_mr,
            )

        gen = self._signal_generator_cls(config, filt)
        # Inject funding rate data if the generator supports it
        if hasattr(gen, "funding_rates") and self._funding_rates is not None:
            gen.funding_rates = self._funding_rates
        signals = _apply_signal_delay(gen.generate(self._data, params))

        bt_config = BacktestConfig(
            symbol=self._symbol,
            interval=self._interval,
            start_date=self._data.index[0].to_pydatetime(),
            end_date=self._data.index[-1].to_pydatetime(),
            initial_capital=self._initial_capital,
            leverage=self._leverage,
        )

        psp = float(getattr(config, "position_size_pct", 1.0))
        bt = VectorizedBacktest(config=bt_config, cost_config=self._cost_config, position_size_pct=psp)
        result = bt.run(
            data=self._data, signals=signals, funding_rates=self._funding_rates
        )

        analyzer = PerformanceAnalyzer(
            equity_curve=result.equity_curve,
            trades=result.trades,
            initial_capital=bt_config.initial_capital,
        )
        metrics = analyzer.calculate_metrics()

        # Annualize trades
        days = (self._data.index[-1] - self._data.index[0]).days
        years = max(days / 365.25, 1 / 365.25)
        total_trades = metrics.get("total_trades", 0)
        ann_trades = total_trades / years if years > 0 else 0

        return CellResult(
            x_value=float(xv),
            y_value=float(yv),
            sharpe_ratio=metrics.get("sharpe_ratio", 0) or 0,
            annualized_return_pct=metrics.get("annualized_return_pct", 0) or 0,
            max_drawdown_pct=metrics.get("max_drawdown_pct", 0) or 0,
            win_rate_pct=metrics.get("win_rate_pct", 0) or 0,
            total_trades=total_trades,
            annualized_trades=round(ann_trades, 1),
            profit_factor=metrics.get("profit_factor", 0) or 0,
            total_return_pct=metrics.get("total_return_pct", 0) or 0,
        )


# ---------------------------------------------------------------------------
# MesaDetector
# ---------------------------------------------------------------------------
class MesaDetector:
    """Detect stable profitable plateau regions (Mesas) in the heatmap."""

    def detect(
        self,
        results: HeatmapResults,
        sharpe_threshold: float = 0.5,
        min_cells: int = 3,
    ) -> List[MesaRegion]:
        all_mesas: List[MesaRegion] = []

        for panel_idx, panel in enumerate(results.panels):
            grid = np.array(panel["sharpe_grid"])

            mask = np.zeros_like(grid, dtype=bool)
            for i in range(grid.shape[0]):
                for j in range(grid.shape[1]):
                    if not np.isnan(grid[i, j]) and grid[i, j] > sharpe_threshold:
                        mask[i, j] = True

            # BFS flood fill to find connected regions
            visited = np.zeros_like(mask, dtype=bool)
            regions: List[List[Tuple[int, int]]] = []

            for i in range(mask.shape[0]):
                for j in range(mask.shape[1]):
                    if mask[i, j] and not visited[i, j]:
                        region = self._bfs(mask, visited, i, j)
                        if len(region) >= min_cells:
                            regions.append(region)

            for region_cells in regions:
                mesa = self._build_mesa(
                    region_cells, results, panel, panel_idx, len(all_mesas)
                )
                all_mesas.append(mesa)

        # Sort by avg_sharpe descending
        all_mesas.sort(key=lambda m: m.avg_sharpe, reverse=True)
        for idx, m in enumerate(all_mesas):
            m.index = idx

        return all_mesas

    @staticmethod
    def _bfs(
        mask: np.ndarray, visited: np.ndarray, si: int, sj: int
    ) -> List[Tuple[int, int]]:
        queue = deque([(si, sj)])
        visited[si, sj] = True
        cells = []
        while queue:
            i, j = queue.popleft()
            cells.append((i, j))
            for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                ni, nj = i + di, j + dj
                if 0 <= ni < mask.shape[0] and 0 <= nj < mask.shape[1]:
                    if mask[ni, nj] and not visited[ni, nj]:
                        visited[ni, nj] = True
                        queue.append((ni, nj))
        return cells

    def _build_mesa(
        self,
        cells: List[Tuple[int, int]],
        results: HeatmapResults,
        panel: Dict[str, Any],
        panel_idx: int,
        mesa_idx: int,
    ) -> MesaRegion:
        grid = np.array(panel["sharpe_grid"])
        metrics = panel["metrics"]

        sharpes = []
        returns = []
        drawdowns = []
        trades_yr = []

        for yi, xi in cells:
            s = grid[yi, xi]
            if not np.isnan(s):
                sharpes.append(s)
            r = metrics["ann_return"][yi][xi]
            if not np.isnan(r):
                returns.append(r)
            d = metrics["max_dd"][yi][xi]
            if not np.isnan(d):
                drawdowns.append(d)
            t = metrics["ann_trades"][yi][xi]
            if not np.isnan(t):
                trades_yr.append(t)

        avg_sharpe = float(np.mean(sharpes)) if sharpes else 0
        std_sharpe = float(np.std(sharpes)) if sharpes else 0
        stability = 1.0 / (1.0 + std_sharpe)

        avg_return = float(np.mean(returns)) if returns else 0
        avg_dd = float(np.mean(drawdowns)) if drawdowns else 0
        avg_trades = float(np.mean(trades_yr)) if trades_yr else 0

        # Ranges in parameter space
        x_vals = [results.x_values[xi] for _, xi in cells]
        y_vals = [results.y_values[yi] for yi, _ in cells]

        # Center = cell with highest Sharpe
        best_idx = int(np.argmax(sharpes)) if sharpes else 0
        center_yi, center_xi = cells[best_idx]

        # Frequency label
        freq_label = _classify_frequency(avg_trades)

        # Extra params from fixed + third_param
        extra = dict(results.fixed_params)
        if panel.get("third_param_name") and panel.get("third_param_value") is not None:
            extra[panel["third_param_name"]] = panel["third_param_value"]

        return MesaRegion(
            index=mesa_idx,
            x_range=(round(min(x_vals), 2), round(max(x_vals), 2)),
            y_range=(round(min(y_vals), 2), round(max(y_vals), 2)),
            center_x=results.x_values[center_xi],
            center_y=results.y_values[center_yi],
            avg_sharpe=round(avg_sharpe, 2),
            stability=round(stability, 2),
            avg_return_pct=round(avg_return, 1),
            avg_max_dd_pct=round(avg_dd, 1),
            avg_trades_yr=round(avg_trades, 1),
            area=len(cells),
            frequency_label=freq_label,
            third_param_name=panel.get("third_param_name"),
            third_param_value=panel.get("third_param_value"),
            panel_index=panel_idx,
            extra_params=extra,
        )


# ---------------------------------------------------------------------------
# FrequencyAnalyzer
# ---------------------------------------------------------------------------
class FrequencyAnalyzer:
    """Classify grid cells into frequency bands and summarize."""

    def analyze(self, results: HeatmapResults) -> Dict[str, Any]:
        band_data: Dict[str, List[float]] = {k: [] for k in FREQUENCY_BANDS}
        band_counts: Dict[str, int] = {k: 0 for k in FREQUENCY_BANDS}

        for panel in results.panels:
            ann_trades_grid = panel["metrics"]["ann_trades"]
            sharpe_grid = panel["sharpe_grid"]
            rows = len(ann_trades_grid)
            cols = len(ann_trades_grid[0]) if rows > 0 else 0

            for i in range(rows):
                for j in range(cols):
                    at = ann_trades_grid[i][j]
                    s = sharpe_grid[i][j]
                    if np.isnan(at) or np.isnan(s):
                        continue
                    band = _classify_frequency(at)
                    band_data[band].append(s)
                    band_counts[band] += 1

        summary = {}
        for band_name in FREQUENCY_BANDS:
            sharpes = band_data[band_name]
            summary[band_name] = {
                "count": band_counts[band_name],
                "avg_sharpe": round(float(np.mean(sharpes)), 2) if sharpes else 0,
            }

        return summary


# ---------------------------------------------------------------------------
# ConfigExporter
# ---------------------------------------------------------------------------
class ConfigExporter:
    """Export Mesa configs to optimized_config.py and heatmap_results.json."""

    def export_python(
        self,
        mesas: List[MesaRegion],
        output_path: Path,
        x_param_name: str = "x",
        y_param_name: str = "y",
        min_hold_from_y: bool = True,
    ) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"# Auto-generated by heatmap scan ({timestamp})",
            "# DO NOT EDIT - re-run heatmap scan to regenerate",
            "",
            "MESA_CONFIGS = [",
        ]
        for m in mesas:
            cy = m.center_y
            if min_hold_from_y:
                min_hold = max(2, int(cy) // 12)
                cooldown = max(1, min_hold // 2)
            else:
                min_hold = m.extra_params.get("min_holding_bars", 16)
                cooldown = m.extra_params.get("cooldown_bars", 8)
            lines.append("    {")
            lines.append(f'        "name": "Mesa #{m.index} ({m.frequency_label})",')
            lines.append(f'        "{x_param_name}": {m.center_x},')
            lines.append(f'        "{y_param_name}": {m.center_y},')
            for k, v in m.extra_params.items():
                lines.append(f'        "{k}": {v},')
            lines.append(f'        "avg_sharpe": {m.avg_sharpe},')
            lines.append(f'        "stability": {m.stability},')
            lines.append(f'        "avg_return_pct": {m.avg_return_pct},')
            lines.append(f'        "avg_max_dd_pct": {m.avg_max_dd_pct},')
            lines.append(f'        "avg_trades_yr": {m.avg_trades_yr},')
            lines.append(f'        "frequency_label": "{m.frequency_label}",')
            lines.append(f'        "min_holding_bars": {min_hold},')
            lines.append(f'        "cooldown_bars": {cooldown},')
            lines.append("    },")
        lines.append("]")
        lines.append("")

        output_path.write_text("\n".join(lines))

    def export_json(
        self,
        results: HeatmapResults,
        mesas: List[MesaRegion],
        freq_summary: Dict[str, Any],
        output_path: Path,
        x_param_name: str = "x",
        y_param_name: str = "y",
    ) -> None:
        mesa_dicts = []
        for m in mesas:
            mesa_dicts.append(
                {
                    "index": m.index,
                    f"{x_param_name}_range": list(m.x_range),
                    f"{y_param_name}_range": list(m.y_range),
                    f"center_{x_param_name}": m.center_x,
                    f"center_{y_param_name}": m.center_y,
                    "center_x": m.center_x,
                    "center_y": m.center_y,
                    "x_range": list(m.x_range),
                    "y_range": list(m.y_range),
                    "avg_sharpe": m.avg_sharpe,
                    "stability": m.stability,
                    "avg_return_pct": m.avg_return_pct,
                    "avg_max_dd_pct": m.avg_max_dd_pct,
                    "avg_trades_yr": m.avg_trades_yr,
                    "area": m.area,
                    "frequency_label": m.frequency_label,
                    "third_param_name": m.third_param_name,
                    "third_param_value": m.third_param_value,
                    "panel_index": m.panel_index,
                    "extra_params": m.extra_params,
                }
            )

        panels_serializable = []
        for p in results.panels:
            panels_serializable.append(
                {
                    "label": p["label"],
                    "third_param_name": p["third_param_name"],
                    "third_param_value": p["third_param_value"],
                    "sharpe_grid": p["sharpe_grid"],
                    "metrics": p["metrics"],
                }
            )

        data = {
            "generated_at": datetime.now().isoformat(),
            "period": results.period,
            "scan_time_s": results.scan_time_s,
            "fixed_params": results.fixed_params,
            "x_param_name": x_param_name,
            "y_param_name": y_param_name,
            "x_values": results.x_values,
            "y_values": results.y_values,
            "zscore_values": results.x_values,
            "hurst_values": results.y_values,
            "panels": panels_serializable,
            "mesas": mesa_dicts,
            "frequency_summary": freq_summary,
        }

        def _default(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return str(obj)

        output_path.write_text(json.dumps(data, indent=2, default=_default))


# ---------------------------------------------------------------------------
# HeatmapReportGenerator
# ---------------------------------------------------------------------------
class HeatmapReportGenerator:
    """Generate interactive HTML report with 6 heatmap panels + Mesa overlay."""

    _METRICS = [
        ("sharpe", "Sharpe Ratio", "RdYlGn", False),
        ("ann_return", "Annualized Return %", "RdYlGn", False),
        ("max_dd", "Max Drawdown %", "RdYlGn", True),
        ("win_rate", "Win Rate %", "Blues", False),
        ("ann_trades", "Trades/Year", "YlOrRd", False),
        ("profit_factor", "Profit Factor", "RdYlGn", False),
    ]

    def generate(
        self,
        results: HeatmapResults,
        mesas: List[MesaRegion],
        freq_summary: Dict[str, Any],
        strategy_name: str = "Strategy",
        x_label: str = "X",
        y_label: str = "Y",
    ) -> str:
        panels_json = self._build_panels_json(results, mesas)
        mesas_json = json.dumps(
            [
                {
                    "index": m.index,
                    "x_range": list(m.x_range),
                    "y_range": list(m.y_range),
                    "center_x": m.center_x,
                    "center_y": m.center_y,
                    "avg_sharpe": m.avg_sharpe,
                    "panel_index": m.panel_index,
                }
                for m in mesas
            ]
        )
        freq_json = json.dumps(freq_summary)
        x_json = json.dumps(results.x_values)
        y_json = json.dumps(results.y_values)
        num_panels = len(results.panels)
        panel_labels = json.dumps([p["label"] for p in results.panels])

        x_label_js = x_label.replace("'", "\\'")
        y_label_js = y_label.replace("'", "\\'")

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{strategy_name} Heatmap Parameter Scan</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 20px; background: #f5f5f5; }}
.header {{ background: linear-gradient(135deg, #1a1a2e, #16213e); color: white; padding: 20px 30px; border-radius: 10px; margin-bottom: 20px; }}
.header h1 {{ margin: 0 0 5px 0; font-size: 24px; }}
.header .sub {{ color: #a0a0c0; font-size: 14px; }}
.card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.tabs {{ display: flex; gap: 5px; margin-bottom: 15px; flex-wrap: wrap; }}
.tab {{ padding: 8px 16px; border: 1px solid #ddd; border-radius: 5px; cursor: pointer; background: #f0f0f0; font-size: 13px; }}
.tab.active {{ background: #1a1a2e; color: white; border-color: #1a1a2e; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
.mesa-card {{ background: #f8f9fa; border-left: 4px solid #28a745; padding: 12px 15px; border-radius: 0 5px 5px 0; margin-bottom: 10px; }}
.mesa-card h4 {{ margin: 0 0 8px 0; color: #1a1a2e; }}
.mesa-card .detail {{ font-size: 13px; color: #555; line-height: 1.6; }}
.freq-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
.freq-table th, .freq-table td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }}
.freq-table th {{ background: #f0f0f0; font-weight: 600; }}
.best-mark {{ background: #e6ffe6; font-weight: 600; }}
.plot-container {{ width: 100%; min-height: 400px; }}
</style>
</head>
<body>
<div class="header">
  <h1>Parameter Heatmap Scan</h1>
  <div class="sub">{strategy_name} | Period: {results.period} | Scan time: {results.scan_time_s}s | Grid: {len(results.x_values)}x{len(results.y_values)}</div>
</div>

<div class="card" id="tab-section" {"style='display:none'" if num_panels <= 1 else ""}>
  <div class="tabs" id="panel-tabs"></div>
</div>

<div class="grid-2" id="heatmap-grids"></div>

<div class="card">
  <h3>Mesa Regions (Stable Profitable Plateaus)</h3>
  <div id="mesa-cards"></div>
</div>

<div class="card">
  <h3>Frequency Distribution</h3>
  <table class="freq-table" id="freq-table">
    <tr><th>Frequency Band</th><th>Cells</th><th>Avg Sharpe</th></tr>
  </table>
</div>

<div style="text-align:center;color:#999;font-size:12px;margin-top:20px;">
  Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} by {strategy_name} Heatmap System
</div>

<script>
const panelsData = {panels_json};
const mesasData = {mesas_json};
const freqData = {freq_json};
const xValues = {x_json};
const yValues = {y_json};
const numPanels = {num_panels};
const panelLabels = {panel_labels};
const xLabel = '{x_label_js}';
const yLabel = '{y_label_js}';

const METRICS = [
  {{key:"sharpe", title:"Sharpe Ratio", colorscale:"RdYlGn", reversed:false}},
  {{key:"ann_return", title:"Annualized Return %", colorscale:"RdYlGn", reversed:false}},
  {{key:"max_dd", title:"Max Drawdown %", colorscale:"RdYlGn", reversed:true}},
  {{key:"win_rate", title:"Win Rate %", colorscale:"Blues", reversed:false}},
  {{key:"ann_trades", title:"Trades/Year", colorscale:"YlOrRd", reversed:false}},
  {{key:"profit_factor", title:"Profit Factor", colorscale:"RdYlGn", reversed:false}}
];

let activePanel = 0;

function buildTabs() {{
  if (numPanels <= 1) return;
  const container = document.getElementById('panel-tabs');
  for (let i = 0; i < numPanels; i++) {{
    const tab = document.createElement('div');
    tab.className = 'tab' + (i === 0 ? ' active' : '');
    tab.textContent = panelLabels[i];
    tab.onclick = () => switchPanel(i);
    container.appendChild(tab);
  }}
}}

function switchPanel(idx) {{
  activePanel = idx;
  document.querySelectorAll('.tab').forEach((t, i) => {{
    t.className = 'tab' + (i === idx ? ' active' : '');
  }});
  renderHeatmaps();
}}

function renderHeatmaps() {{
  const container = document.getElementById('heatmap-grids');
  container.innerHTML = '';
  const panel = panelsData[activePanel];

  METRICS.forEach((m, mi) => {{
    const card = document.createElement('div');
    card.className = 'card';
    const plotDiv = document.createElement('div');
    plotDiv.id = 'plot-' + m.key;
    plotDiv.className = 'plot-container';
    card.appendChild(plotDiv);
    container.appendChild(card);

    const z = panel.metrics[m.key];
    const cs = m.reversed ? [[0,'rgb(0,104,55)'],[0.5,'rgb(255,255,191)'],[1,'rgb(165,0,38)']]
                          : (m.colorscale === 'Blues' ? 'Blues' : (m.colorscale === 'YlOrRd' ? 'YlOrRd' : 'RdYlGn'));

    const traces = [{{
      z: z,
      x: xValues.map(v => typeof v === 'number' ? v.toFixed(2) : v),
      y: yValues,
      type: 'heatmap',
      colorscale: cs,
      hovertemplate: xLabel + ': %{{x}}<br>' + yLabel + ': %{{y}}<br>' + m.title + ': %{{z:.2f}}<extra></extra>',
      colorbar: {{title: m.title, len: 0.8}}
    }}];

    const shapes = [];
    const annotations = [];
    mesasData.forEach(mesa => {{
      if (mesa.panel_index !== activePanel) return;
      shapes.push({{
        type: 'rect',
        x0: mesa.x_range[0].toFixed(2),
        x1: mesa.x_range[1].toFixed(2),
        y0: mesa.y_range[0],
        y1: mesa.y_range[1],
        line: {{color: 'white', width: 2, dash: 'dash'}},
        fillcolor: 'rgba(0,0,0,0)'
      }});
      annotations.push({{
        x: mesa.center_x.toFixed(2),
        y: mesa.center_y,
        text: 'M#' + mesa.index,
        showarrow: false,
        font: {{color: 'white', size: 12, family: 'Arial Black'}}
      }});
    }});

    const layout = {{
      title: {{text: m.title, font: {{size: 14}}}},
      xaxis: {{title: xLabel, tickangle: -45}},
      yaxis: {{title: yLabel}},
      shapes: shapes,
      annotations: annotations,
      margin: {{t: 40, b: 60, l: 60, r: 80}},
      height: 400
    }};

    Plotly.newPlot(plotDiv, traces, layout, {{responsive: true}});
  }});
}}

function renderMesas() {{
  const container = document.getElementById('mesa-cards');
  if (mesasData.length === 0) {{
    container.innerHTML = '<p style="color:#999">No Mesa regions found. Try lowering the Sharpe threshold.</p>';
    return;
  }}
  mesasData.forEach(m => {{
    const card = document.createElement('div');
    card.className = 'mesa-card';
    card.innerHTML = '<h4>Mesa #' + m.index + '</h4>' +
      '<div class="detail">' +
      xLabel + ': [' + m.x_range[0].toFixed(2) + ', ' + m.x_range[1].toFixed(2) + '] &nbsp; ' +
      yLabel + ': [' + m.y_range[0] + ', ' + m.y_range[1] + ']<br>' +
      'Avg Sharpe: ' + m.avg_sharpe.toFixed(2) + ' &nbsp; ' +
      'Center: ' + xLabel + '=' + m.center_x.toFixed(2) + ', ' + yLabel + '=' + m.center_y +
      '</div>';
    container.appendChild(card);
  }});
}}

function renderFrequency() {{
  const table = document.getElementById('freq-table');
  let bestBand = '';
  let bestSharpe = -Infinity;
  for (const [band, data] of Object.entries(freqData)) {{
    if (data.count > 0 && data.avg_sharpe > bestSharpe) {{
      bestSharpe = data.avg_sharpe;
      bestBand = band;
    }}
  }}
  for (const [band, data] of Object.entries(freqData)) {{
    const row = table.insertRow();
    const cls = band === bestBand ? ' class="best-mark"' : '';
    row.innerHTML = '<td' + cls + '>' + band + (band === bestBand ? ' (Best)' : '') + '</td>' +
      '<td' + cls + '>' + data.count + '</td>' +
      '<td' + cls + '>' + data.avg_sharpe.toFixed(2) + '</td>';
  }}
}}

buildTabs();
renderHeatmaps();
renderMesas();
renderFrequency();
</script>
</body>
</html>"""
        return html

    def _build_panels_json(
        self, results: HeatmapResults, mesas: List[MesaRegion]
    ) -> str:
        panels = []
        for panel in results.panels:
            panels.append(
                {
                    "label": panel["label"],
                    "metrics": panel["metrics"],
                }
            )
        return json.dumps(
            panels,
            default=lambda o: None if isinstance(o, float) and np.isnan(o) else o,
        )

    def save(self, html: str, output_path: Path) -> None:
        output_path.write_text(html)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _classify_frequency(trades_per_year: float) -> str:
    for band_name, (low, high) in FREQUENCY_BANDS.items():
        if low <= trades_per_year < high:
            return band_name
    return "Yearly (<4/yr)"


def _print_terminal_output(
    results: HeatmapResults,
    mesas: List[MesaRegion],
    freq_summary: Dict[str, Any],
    x_label: str = "X",
    y_label: str = "Y",
) -> None:
    """Print terminal summary of heatmap scan results."""
    n_x = len(results.x_values)
    n_y = len(results.y_values)
    n_panels = len(results.panels)
    total = n_x * n_y * n_panels

    print()
    print("=" * 60)
    print("HEATMAP PARAMETER SCAN")
    print("=" * 60)
    print(f"Grid: {n_x} x {n_y} x {n_panels} panels = {total} combinations")
    print(f"Period: {results.period}")
    print(f"Scan time: {results.scan_time_s}s")

    print()
    print("=" * 60)
    print("MESA REGIONS (Stable Profitable Areas)")
    print("=" * 60)

    if not mesas:
        print("  No Mesa regions found.")
    else:
        for m in mesas:
            print(
                f"  #{m.index}  {x_label}: [{m.x_range[0]:.2f}, {m.x_range[1]:.2f}]  "
                f"{y_label}: [{m.y_range[0]}, {m.y_range[1]}]"
            )
            if m.third_param_name:
                print(
                    f"      {m.third_param_name}: {m.third_param_value} (Panel {m.panel_index})"
                )
            print(
                f"      Sharpe: {m.avg_sharpe:.2f} avg  Stability: {m.stability:.2f}  Area: {m.area} cells"
            )
            print(
                f"      Return: {m.avg_return_pct:+.1f}%/yr  MaxDD: {m.avg_max_dd_pct:.1f}%  "
                f"Trades: {m.avg_trades_yr:.0f}/yr"
            )
            print(f"      Frequency: {m.frequency_label}")
            print(
                f"      -> Center: {x_label}={m.center_x:.2f}, {y_label}={m.center_y}"
            )
            print()

    print("=" * 60)
    print("FREQUENCY DISTRIBUTION")
    print("=" * 60)

    best_band = ""
    best_sharpe = -float("inf")
    for band_name, data in freq_summary.items():
        if data["count"] > 0 and data["avg_sharpe"] > best_sharpe:
            best_sharpe = data["avg_sharpe"]
            best_band = band_name

    for band_name, data in freq_summary.items():
        mark = " <- Best" if band_name == best_band else ""
        print(
            f"  {band_name:<25} {data['count']:>4} cells  avg Sharpe: {data['avg_sharpe']:>6.2f}{mark}"
        )

    print("=" * 60)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_heatmap_scan(
    data: pd.DataFrame,
    signal_generator_cls,
    config_cls,
    filter_config_cls,
    funding_rates: Optional[pd.DataFrame] = None,
    period: str = "1y",
    resolution: int = 15,
    third_param: Optional[str] = None,
    third_param_choices: Optional[Dict[str, List]] = None,
    all_regimes: bool = False,
    output_dir: Path = None,
    strategy_name: str = "Strategy",
    x_param_name: str = "x",
    y_param_name: str = "y",
    x_range: Tuple[float, float] = (1.5, 5.0),
    y_range: Tuple[float, float] = (20, 200),
    x_label: str = "X",
    y_label: str = "Y",
    fixed_params: Optional[Dict[str, Any]] = None,
    filter_config_factory: Optional[Callable] = None,
    symbol: str = "BTC/USDT:USDT",
    cost_config: Optional[CostConfig] = None,
    interval: KlineInterval = KlineInterval.MINUTE_15,
    initial_capital: float = 10000.0,
    leverage: float = 1.0,
) -> None:
    """
    Run complete heatmap scan pipeline.

    This is the exchange-agnostic version with ``symbol``, ``cost_config``,
    ``interval``, and ``initial_capital`` parameters so the BacktestRunner
    can thread exchange profile through it.
    """
    if output_dir is None:
        raise ValueError("output_dir is required")

    if fixed_params is None:
        fixed_params = {}

    print()
    print("=" * 60)
    print(f"HEATMAP PARAMETER SCAN - {strategy_name}")
    print("=" * 60)

    # Build grid
    x_values = np.linspace(x_range[0], x_range[1], resolution)
    y_values = np.array([v for v in np.linspace(y_range[0], y_range[1], resolution)])

    # Determine third-param
    third_param_name = None
    third_param_values = None
    if third_param and third_param_choices and third_param in third_param_choices:
        third_param_name = third_param
        third_param_values = third_param_choices[third_param]
        fixed_params.pop(third_param, None)

    n_panels = len(third_param_values) if third_param_values else 1
    total = resolution * resolution * n_panels
    print(
        f"Grid: {resolution} x {resolution} x {n_panels} panels = {total} combinations"
    )
    print(f"Period: {period}")
    print(f"Data: {data.index[0].date()} to {data.index[-1].date()} ({len(data)} bars)")

    # Scan
    scanner = HeatmapScanner(
        data=data,
        signal_generator_cls=signal_generator_cls,
        config_cls=config_cls,
        filter_config_cls=filter_config_cls,
        funding_rates=funding_rates,
        x_param_name=x_param_name,
        y_param_name=y_param_name,
        filter_config_factory=filter_config_factory,
        symbol=symbol,
        cost_config=cost_config,
        interval=interval,
        initial_capital=initial_capital,
        leverage=leverage,
    )
    results = scanner.scan(
        x_values=x_values,
        y_values=y_values,
        fixed_params=fixed_params,
        third_param_name=third_param_name,
        third_param_values=third_param_values,
    )
    results.period = period

    # Detect Mesas
    detector = MesaDetector()
    mesas = detector.detect(results)
    results.mesas = mesas

    # Frequency analysis
    freq_analyzer = FrequencyAnalyzer()
    freq_summary = freq_analyzer.analyze(results)
    results.frequency_summary = freq_summary

    # Terminal output
    _print_terminal_output(results, mesas, freq_summary, x_label, y_label)

    # Export configs
    exporter = ConfigExporter()
    optimized_config_file = output_dir / "optimized_config.py"
    heatmap_results_file = output_dir / "heatmap_results.json"
    heatmap_report_file = output_dir / "heatmap_report.html"

    if mesas:
        exporter.export_python(
            mesas,
            optimized_config_file,
            x_param_name=x_param_name,
            y_param_name=y_param_name,
        )
        print(f"\nConfigs exported to: {optimized_config_file}")
    else:
        print("\nNo Mesa regions found - skipping config export.")

    exporter.export_json(
        results,
        mesas,
        freq_summary,
        heatmap_results_file,
        x_param_name=x_param_name,
        y_param_name=y_param_name,
    )
    print(f"Data:   {heatmap_results_file}")

    # HTML report
    report_gen = HeatmapReportGenerator()
    html = report_gen.generate(
        results,
        mesas,
        freq_summary,
        strategy_name=strategy_name,
        x_label=x_label,
        y_label=y_label,
    )
    report_gen.save(html, heatmap_report_file)
    print(f"Report: {heatmap_report_file}")

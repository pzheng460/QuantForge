"""Statistical report from simulation / stress-test results."""

from __future__ import annotations

from typing import Optional

import numpy as np


class SimulationReport:
    """Aggregate and summarise metrics from multiple simulation paths.

    Parameters
    ----------
    metrics_list : list[dict[str, float]]
        One metrics dict per simulated path (keys such as
        ``total_return_pct``, ``sharpe_ratio``, ``max_drawdown_pct``).
    weights : np.ndarray, optional
        Importance weights (e.g. from stress testing).  When provided,
        summary statistics are computed as weighted quantities.
    """

    def __init__(
        self,
        metrics_list: list[dict[str, float]],
        weights: Optional[np.ndarray] = None,
    ) -> None:
        if not metrics_list:
            raise ValueError("metrics_list must not be empty")
        self._metrics_list = metrics_list
        self._keys = sorted(metrics_list[0].keys())
        self._arrays: dict[str, np.ndarray] = {
            k: np.array([m[k] for m in metrics_list], dtype=np.float64)
            for k in self._keys
        }
        if weights is not None:
            w = np.asarray(weights, dtype=np.float64)
            self._weights = w / w.sum()  # normalise to sum=1
        else:
            self._weights = np.ones(len(metrics_list)) / len(metrics_list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, dict[str, float]]:
        """Per-metric summary statistics.

        Returns a dict keyed by metric name, each containing:
        mean, median, std, min, max, p5, p25, p75, p95.
        """
        result: dict[str, dict[str, float]] = {}
        for k, arr in self._arrays.items():
            w = self._weights
            wmean = float(np.sum(w * arr))
            result[k] = {
                "mean": wmean,
                "median": float(self._weighted_percentile(arr, 50)),
                "std": float(np.sqrt(np.sum(w * (arr - wmean) ** 2))),
                "min": float(np.min(arr)),
                "max": float(np.max(arr)),
                "p5": float(self._weighted_percentile(arr, 5)),
                "p25": float(self._weighted_percentile(arr, 25)),
                "p75": float(self._weighted_percentile(arr, 75)),
                "p95": float(self._weighted_percentile(arr, 95)),
            }
        return result

    def confidence_interval(
        self, metric: str, confidence: float = 0.95
    ) -> tuple[float, float]:
        """Weighted percentile-based confidence interval.

        Parameters
        ----------
        metric : str
            Name of the metric (must be a key in the metrics dicts).
        confidence : float
            Confidence level in (0, 1).  Default 0.95 → 2.5th – 97.5th.

        Returns
        -------
        (lower, upper) : tuple[float, float]
        """
        if metric not in self._arrays:
            raise KeyError(f"Unknown metric: {metric!r}")
        alpha = (1 - confidence) / 2 * 100
        arr = self._arrays[metric]
        lo = float(self._weighted_percentile(arr, alpha))
        hi = float(self._weighted_percentile(arr, 100 - alpha))
        return (lo, hi)

    def plot_distributions(
        self,
        metrics: Optional[list[str]] = None,
        save_path: Optional[str] = None,
    ) -> None:
        """Plot histograms with confidence-interval bands.

        Requires *matplotlib*.  If matplotlib is not installed the method
        silently returns without error.

        Parameters
        ----------
        metrics : list[str], optional
            Subset of metric names to plot.  Defaults to all.
        save_path : str, optional
            If provided, save the figure to this path instead of showing.
        """
        try:
            import matplotlib.pyplot as plt  # noqa: F401
        except ImportError:
            return

        keys = metrics if metrics is not None else self._keys
        n = len(keys)
        cols = min(n, 3)
        rows = (n + cols - 1) // cols

        fig, axes = plt.subplots(
            rows, cols, figsize=(5 * cols, 4 * rows), squeeze=False
        )

        for idx, key in enumerate(keys):
            ax = axes[idx // cols][idx % cols]
            arr = self._arrays[key]
            ax.hist(arr, bins=30, alpha=0.7, edgecolor="black")
            lo, hi = self.confidence_interval(key, 0.95)
            ax.axvline(lo, color="red", linestyle="--", label="95% CI")
            ax.axvline(hi, color="red", linestyle="--")
            ax.set_title(key)
            ax.legend(fontsize=8)

        # Hide unused axes
        for idx in range(n, rows * cols):
            axes[idx // cols][idx % cols].set_visible(False)

        fig.tight_layout()
        if save_path:
            fig.savefig(save_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        else:
            plt.show()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _weighted_percentile(self, arr: np.ndarray, pct: float) -> float:
        """Compute weighted percentile using linear interpolation."""
        order = np.argsort(arr)
        sorted_arr = arr[order]
        sorted_w = self._weights[order]
        cum_w = np.cumsum(sorted_w)
        # Normalise cumulative weights to [0, 100] scale
        cum_pct = (cum_w - sorted_w / 2) * 100  # midpoint convention
        return float(np.interp(pct, cum_pct, sorted_arr))

"""Monte Carlo simulation, bootstrap resampling, and stress testing."""

from nexustrader.backtest.simulation.bootstrap import BlockBootstrap
from nexustrader.backtest.simulation.monte_carlo import (
    GBMGenerator,
    JumpDiffusionGenerator,
)
from nexustrader.backtest.simulation.report import SimulationReport
from nexustrader.backtest.simulation.stress_test import (
    StressTestGenerator,
    StressTestResult,
)

__all__ = [
    "BlockBootstrap",
    "GBMGenerator",
    "JumpDiffusionGenerator",
    "SimulationReport",
    "StressTestGenerator",
    "StressTestResult",
]

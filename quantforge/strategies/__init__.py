"""Built-in, reviewed Python strategies."""

from quantforge.strategies.technical import (
    BBSqueeze,
    BBSqueezeV2,
    BollingerBand,
    BollingerBandV4,
    DualRegime,
    EMACrossover,
    EMACrossoverV2,
    EMACrossoverV3,
    HurstKalman,
    MACDTrend,
    MomentumADX,
    RSIMomentum,
    SMATrend,
)
from quantforge.strategies.tsla_nvda_options import TslaNvdaOptionsManager

__all__ = [
    "BBSqueeze",
    "BBSqueezeV2",
    "BollingerBand",
    "BollingerBandV4",
    "DualRegime",
    "EMACrossover",
    "EMACrossoverV2",
    "EMACrossoverV3",
    "HurstKalman",
    "MACDTrend",
    "MomentumADX",
    "RSIMomentum",
    "SMATrend",
    "TslaNvdaOptionsManager",
]

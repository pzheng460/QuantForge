# DEPRECATED — not actively used. Kept for reference.
"""MA Convergence (均线密集) strategy.

Uses 6 moving averages (SMA 20/60/120 + EMA 20/60/120) to detect consolidation
zones. Breakout from the convergence zone triggers entries via two methods:
  1. MA Convergence Breakout (均线密集开仓法)
  2. First MA20 Retest (第一次回踩20均线不破开仓法)
"""

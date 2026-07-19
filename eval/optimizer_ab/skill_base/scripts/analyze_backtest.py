#!/usr/bin/env python3
"""Parse QuantForge Pine backtest output into compact JSON for agent review."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from validate_pine import classify_strategy


def parse_metrics(text: str) -> dict[str, float | int | str]:
    patterns = {
        "return_pct": r"Return:\s+([\d.-]+)%",
        "total_trades": r"Total Trades:\s+(\d+)",
        "win_rate": r"Win Rate:\s+([\d.]+)%",
        "profit_factor": r"Profit Factor:\s+([\d.]+)",
        "max_drawdown": r"Max Drawdown:\s+([\d.]+)%",
        "net_pnl": r"Net P&L:\s+\$([\d,.-]+)",
    }
    metrics: dict[str, float | int | str] = {}
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if not match:
            continue
        value = match.group(1).replace(",", "")
        metrics[key] = int(value) if value.isdigit() else float(value)
    return metrics


def failures(metrics: dict[str, float | int | str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    pf = float(metrics.get("profit_factor", 0.0))
    mdd = float(metrics.get("max_drawdown", 0.0))
    wr = float(metrics.get("win_rate", 0.0))
    trades = int(metrics.get("total_trades", 0))
    if pf < 1.2:
        out.append(
            {
                "type": "LOW_PF",
                "severity": "high",
                "constraint_hint": "improve expectancy or pause",
            }
        )
    if mdd > 15:
        out.append(
            {
                "type": "HIGH_DD",
                "severity": "high",
                "constraint_hint": "reduce exposure or add stop",
            }
        )
    if wr < 30:
        out.append(
            {
                "type": "LOW_WIN_RATE",
                "severity": "medium",
                "constraint_hint": "filter noisy entries",
            }
        )
    if trades < 20:
        out.append(
            {
                "type": "LOW_SAMPLE",
                "severity": "medium",
                "constraint_hint": "avoid accepting sparse validation",
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pine-file", default="")
    args = parser.parse_args()
    text = sys.stdin.read()
    metrics = parse_metrics(text)
    strategy_type = "unknown"
    if args.pine_file and Path(args.pine_file).exists():
        strategy_type = classify_strategy(Path(args.pine_file).read_text())
    print(
        json.dumps(
            {
                "metrics": metrics,
                "failures": failures(metrics),
                "strategy_classification": strategy_type,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

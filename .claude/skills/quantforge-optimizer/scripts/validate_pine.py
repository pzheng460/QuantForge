#!/usr/bin/env python3
"""Repo-local Pine validator used by the optimizer harness."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def classify_strategy(source: str) -> str:
    lower = source.lower()
    trend = (
        "ta.crossover" in source
        or "ta.crossunder" in source
        or source.count("ta.ema") >= 2
    )
    reversion = "ta.rsi" in source or "ta.stdev" in source or "reversion" in lower
    if trend and reversion:
        return "hybrid"
    if trend:
        return "trend_following"
    if reversion:
        return "mean_reversion"
    return "unknown"


def validate(source: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def warn(code: str, message: str) -> None:
        issues.append({"level": "warning", "code": code, "message": message})

    def error(code: str, message: str) -> None:
        issues.append({"level": "error", "code": code, "message": message})

    if "strategy(" not in source:
        error("NO_STRATEGY", "Missing strategy() declaration")
        return issues
    if "ta.adx" not in source and "filter" not in source.lower():
        warn(
            "NO_REGIME_FILTER",
            "No regime filter (ADX/volatility) — vulnerable to whipsaw in ranging markets",
        )
    if "strategy.exit" not in source:
        warn(
            "NO_STOP_LOSS",
            "No stop-loss detected — consider strategy.exit() with stop or ATR-based stops",
        )
    if "qty" not in source and "strategy.entry" in source:
        warn("NO_POSITION_SIZE", "No explicit position sizing — using platform default")
    inputs = re.findall(
        r"(\w+)\s*=\s*input\.(?:int|float|bool|string|source)\(", source
    )
    for name in inputs:
        if len(re.findall(r"\b" + re.escape(name) + r"\b", source)) <= 1:
            warn("UNUSED_INPUT", f"Input '{name}' is declared but never referenced")
    return issues


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: validate_pine.py <strategy.pine>", file=sys.stderr)
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1
    source = path.read_text()
    issues = validate(source)
    for issue in issues:
        icon = "ERROR" if issue["level"] == "error" else "WARN"
        print(f"{icon} [{issue['code']}] {issue['message']}")
    errors = [i for i in issues if i["level"] == "error"]
    print(f"\n{len(errors)} error(s), {len(issues) - len(errors)} warning(s)")
    print(f"Strategy type: {classify_strategy(source)}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

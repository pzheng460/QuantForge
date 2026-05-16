"""Automatic strategy health gate and re-optimization launcher.

This module is intentionally conservative: by default it evaluates and prints
the next action, but it only launches optimizer agents when --execute is set.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from eval.optimizer_ab.holdout_eval import _window_start_idx, backtest, max_drawdown, profit_factor


@dataclass(frozen=True)
class HealthMetrics:
    return_pct: float
    profit_factor: float
    max_drawdown: float
    win_rate: float
    n_trades: int
    largest_win: float
    net_profit: float

    @property
    def single_trade_concentration(self) -> float:
        if self.net_profit <= 0:
            return 1.0 if self.largest_win > 0 else 0.0
        return max(0.0, self.largest_win) / self.net_profit


@dataclass(frozen=True)
class GateConfig:
    min_profit_factor: float = 1.3
    max_drawdown: float = 0.12
    min_win_rate: float = 0.30
    min_trades: int = 30
    max_single_trade_concentration: float = 0.50


@dataclass(frozen=True)
class Decision:
    action: str
    reasons: list[str]
    score: float


@dataclass(frozen=True)
class WindowHealth:
    name: str
    start: str
    end: str
    metrics: HealthMetrics
    decision: Decision


NEWS_RISK_KEYWORDS = {
    "volatility": 22,
    "liquidation": 20,
    "hack": 30,
    "exploit": 30,
    "regulation": 18,
    "ban": 25,
    "lawsuit": 18,
    "etf": 12,
    "fed": 14,
    "rate": 10,
    "inflation": 12,
    "macro": 10,
    "exchange outage": 25,
}


def decide_action(metrics: HealthMetrics, gate: GateConfig) -> Decision:
    reasons: list[str] = []
    if metrics.profit_factor < gate.min_profit_factor:
        reasons.append("profit_factor")
    if metrics.max_drawdown > gate.max_drawdown:
        reasons.append("max_drawdown")
    if metrics.win_rate < gate.min_win_rate:
        reasons.append("win_rate")
    if metrics.n_trades < gate.min_trades:
        reasons.append("trade_count")
    if metrics.single_trade_concentration > gate.max_single_trade_concentration:
        reasons.append("single_trade_concentration")

    score = 100.0
    score -= max(0.0, gate.min_profit_factor - metrics.profit_factor) * 35
    score -= max(0.0, metrics.max_drawdown - gate.max_drawdown) * 180
    score -= max(0.0, gate.min_win_rate - metrics.win_rate) * 80
    score -= max(0.0, metrics.single_trade_concentration - gate.max_single_trade_concentration) * 45
    score -= max(0, gate.min_trades - metrics.n_trades) * 0.8
    score = max(0.0, round(score, 1))

    if "profit_factor" in reasons or "trade_count" in reasons:
        action = "pause"
    elif reasons:
        action = "reoptimize"
    else:
        action = "observe"
    return Decision(action=action, reasons=reasons, score=score)


def parse_window_specs(value: str) -> list[tuple[str, str, str]]:
    specs: list[tuple[str, str, str]] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split(":")
        if len(parts) != 3:
            raise ValueError(f"window must be name:start:end, got {raw!r}")
        specs.append((parts[0], parts[1], parts[2]))
    return specs


def score_news_events(events: list[dict[str, Any]], *, symbol: str) -> dict[str, Any]:
    base = symbol.split("/")[0].upper()
    score = 0
    matched = []
    keywords: set[str] = set()
    components = {
        "keywords": 0,
        "exchange_status": 0,
        "funding": 0,
        "open_interest": 0,
        "liquidation": 0,
    }
    reasons: set[str] = set()
    for event in events:
        symbols = {str(s).upper() for s in event.get("symbols", [])}
        text = " ".join(str(event.get(k, "")) for k in ("title", "summary", "body")).lower()
        relevant = symbol.upper() in symbols or base in symbols or base.lower() in text
        if not relevant:
            continue
        event_score = 0
        for keyword, weight in NEWS_RISK_KEYWORDS.items():
            if keyword in text:
                event_score += weight
                components["keywords"] += weight
                keywords.add(keyword)
                reasons.add("keywords")
        structured = _score_structured_event(text)
        for key, value in structured.items():
            if value:
                components[key] += value
                reasons.add(key)
                event_score += value
        if event_score:
            matched.append(event)
            score += event_score

    risk_level = "low"
    if score >= 35:
        risk_level = "high"
    elif score >= 18:
        risk_level = "medium"
    return {
        "risk_score": score,
        "risk_level": risk_level,
        "matched_events": len(matched),
        "keywords": sorted(keywords),
        "components": components,
        "reasons": sorted(reasons),
        "events": matched,
    }


def _score_structured_event(text: str) -> dict[str, int]:
    score = {
        "exchange_status": 0,
        "funding": 0,
        "open_interest": 0,
        "liquidation": 0,
    }
    if any(token in text for token in ["degraded", "outage", "maintenance", "api latency", "elevated latency"]):
        score["exchange_status"] += 30
    if "funding_rate=" in text or "funding high" in text:
        score["funding"] += 18
    if "open_interest=" in text or "open interest expansion" in text:
        score["open_interest"] += 14
    if "liquidation" in text or "notional_usd=" in text:
        score["liquidation"] += 25
    return score


def load_news_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not path.exists():
        raise FileNotFoundError(path)
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def build_evidence_report(
    windows: list[WindowHealth],
    news_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trigger_reasons = sorted({reason for window in windows for reason in window.decision.reasons})
    worst = min(windows, key=lambda w: w.decision.score) if windows else None
    action = "observe"
    if any(w.decision.action == "pause" for w in windows):
        action = "pause"
    elif any(w.decision.action == "reoptimize" for w in windows):
        action = "reoptimize"
    if news_risk and news_risk.get("risk_level") == "high" and action == "observe":
        action = "reoptimize"
        trigger_reasons.append("news_risk")
    return {
        "windows": [
            {
                "name": w.name,
                "start": w.start,
                "end": w.end,
                "metrics": asdict(w.metrics) | {
                    "single_trade_concentration": w.metrics.single_trade_concentration,
                },
                "decision": asdict(w.decision),
            }
            for w in windows
        ],
        "worst_window": worst.name if worst else None,
        "trigger_reasons": sorted(set(trigger_reasons)),
        "news_risk": news_risk or {
            "risk_score": 0,
            "risk_level": "none",
            "matched_events": 0,
            "keywords": [],
            "events": [],
        },
        "decision": {
            "action": action,
            "score": worst.decision.score if worst else 100.0,
        },
    }


def evaluate_health(pine_path: Path, symbol: str, exchange: str, timeframe: str, start: str, end: str) -> HealthMetrics:
    result, bars = backtest(pine_path, symbol, exchange, timeframe, start, end)
    win_idx = _window_start_idx(bars, start)
    equity = list(getattr(result, "equity_curve", []))[win_idx:]
    trades = [t for t in getattr(result, "trades", []) if getattr(t, "entry_bar", -1) >= win_idx]
    initial = float(equity[0]) if equity else float(getattr(result, "initial_capital", 100000.0))
    net = sum(float(getattr(t, "pnl", 0.0)) for t in trades)
    wins = [float(getattr(t, "pnl", 0.0)) for t in trades if float(getattr(t, "pnl", 0.0)) > 0]
    n_trades = len(trades)
    return HealthMetrics(
        return_pct=(net / initial) if initial > 0 else 0.0,
        profit_factor=profit_factor(trades),
        max_drawdown=max_drawdown(equity),
        win_rate=(len(wins) / n_trades) if n_trades else 0.0,
        n_trades=n_trades,
        largest_win=max(wins) if wins else 0.0,
        net_profit=net,
    )


def build_orchestrate_command(
    *,
    strategy: str,
    regime: str,
    seeds: str,
    providers: str,
    methods: str = "baseline,cross_review_guided",
    tier: str = "dev",
    execute_holdout: bool = True,
    results_csv: str = "eval/optimizer_ab/results/auto_tune.csv",
    trials_dir: str = "eval/optimizer_ab/results/auto_tune_trials",
) -> list[str]:
    cmd = [
        "uv", "run", "python", "-m", "eval.optimizer_ab.orchestrate",
        "--tier", tier,
        "--methods", methods,
        "--strategies", strategy,
        "--regimes", regime,
        "--seeds", seeds,
        "--agent-providers", providers,
        "--results-csv", results_csv,
        "--trials-dir", trials_dir,
    ]
    if not execute_holdout:
        cmd.append("--no-holdout")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate strategy health and optionally launch auto re-optimization.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the auto-tune health gate.")
    run.add_argument("--pine", required=True, help="Pine file to evaluate.")
    run.add_argument("--strategy", required=True, help="Strategy stem/path for optimizer A/B.")
    run.add_argument("--symbol", default="BTC/USDT:USDT")
    run.add_argument("--exchange", default="bitget")
    run.add_argument("--timeframe", default="1h")
    run.add_argument("--start", default="2024-07-01")
    run.add_argument("--end", default="2024-12-31")
    run.add_argument("--windows", default="", help="Comma-separated name:start:end windows to collect.")
    run.add_argument("--news-file", default="", help="Optional JSONL news/events file for exogenous risk scoring.")
    run.add_argument("--out", default="", help="Optional JSON path to write the evidence report.")
    run.add_argument("--regime", default="trend_2024h1")
    run.add_argument("--seeds", default="1")
    run.add_argument("--providers", default="claude,codex")
    run.add_argument("--optimizer-results-csv", default="eval/optimizer_ab/results/auto_tune.csv")
    run.add_argument("--optimizer-trials-dir", default="eval/optimizer_ab/results/auto_tune_trials")
    run.add_argument("--execute", action="store_true", help="Actually launch optimizer when the gate requests it.")
    run.add_argument("--no-holdout", action="store_true", help="Skip optimizer holdout evaluation.")
    args = parser.parse_args()

    window_specs = parse_window_specs(args.windows) if args.windows else [("current", args.start, args.end)]
    windows = []
    for name, start, end in window_specs:
        metrics = evaluate_health(Path(args.pine), args.symbol, args.exchange, args.timeframe, start, end)
        windows.append(WindowHealth(name, start, end, metrics, decide_action(metrics, GateConfig())))

    news_risk = None
    if args.news_file:
        news_risk = score_news_events(load_news_events(Path(args.news_file)), symbol=args.symbol)
    evidence = build_evidence_report(windows, news_risk)
    cmd = build_orchestrate_command(
        strategy=args.strategy,
        regime=args.regime,
        seeds=args.seeds,
        providers=args.providers,
        execute_holdout=not args.no_holdout,
        results_csv=args.optimizer_results_csv,
        trials_dir=args.optimizer_trials_dir,
    )
    report = {
        "evidence": evidence,
        "decision": evidence["decision"],
        "optimizer_command": cmd,
        "executed": False,
    }

    if args.execute and evidence["decision"]["action"] == "reoptimize":
        report["executed"] = True
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return subprocess.run(cmd, cwd=str(Path(__file__).resolve().parents[1])).returncode

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

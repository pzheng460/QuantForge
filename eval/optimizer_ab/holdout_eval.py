"""Air-gapped OOS evaluator. Reads a trial JSON from runner.py, runs the
optimized .pine on both train and holdout windows, writes metrics back.

The agent's process never imports this module — that's the air gap."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = Path(__file__).resolve().parent

BARS_PER_YEAR = {
    "1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040,
    "30m": 17520, "1h": 8760, "2h": 4380, "4h": 2190, "1d": 365,
}


def backtest(pine_path, symbol, exchange, timeframe, start, end):
    """Run the Pine backtest. Returns (result, bars). Bars include warmup
    so the caller can identify which equity_curve / trade entries belong
    to the requested [start, end] window vs. the warmup prefix."""
    from quantforge.pine.cli import _fetch_ohlcv
    from quantforge.pine.interpreter.context import BarData, ExecutionContext
    from quantforge.pine.interpreter.runtime import PineRuntime
    from quantforge.pine.parser.parser import parse

    raw = _fetch_ohlcv(symbol=symbol, exchange_id=exchange, timeframe=timeframe,
                       start=start, end=end, warmup_days=60)
    bars = [BarData(open=b[1], high=b[2], low=b[3], close=b[4],
                    volume=b[5], time=int(b[0]) // 1000) for b in raw]
    ast = parse(pine_path.read_text())
    return PineRuntime(ExecutionContext(bars=bars)).run(ast), bars


def _window_start_idx(bars, start_date):
    """First bar index whose time >= start_date (UTC midnight). Bars before
    this index are warmup data the runtime processed only to initialize
    indicators; their trades/equity must be excluded from window metrics."""
    start_unix = int(datetime.strptime(start_date, "%Y-%m-%d").replace(
        tzinfo=timezone.utc).timestamp())
    for i, b in enumerate(bars):
        if b.time >= start_unix:
            return i
    return len(bars)


def max_drawdown(equity):
    if not equity:
        return 0.0
    peak, mdd = equity[0], 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0 and (peak - v) / peak > mdd:
            mdd = (peak - v) / peak
    return mdd


def sharpe(equity, bars_per_year):
    if len(equity) < 2:
        return 0.0
    rets = [(equity[i] - equity[i - 1]) / equity[i - 1]
            for i in range(1, len(equity)) if equity[i - 1] > 0]
    if not rets:
        return 0.0
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / len(rets)
    sd = math.sqrt(var) if var > 0 else 0.0
    return 0.0 if sd == 0 else (m / sd) * math.sqrt(bars_per_year)


def profit_factor(trades):
    gp = sum(t.pnl for t in trades if getattr(t, "pnl", 0) > 0)
    gl = abs(sum(t.pnl for t in trades if getattr(t, "pnl", 0) < 0))
    if gl > 0:
        return gp / gl
    return 9999.0 if gp > 0 else 0.0


def evaluate(pine_path, symbol, exchange, timeframe, start, end):
    """Backtest pine_path on [start - warmup, end] but compute metrics
    ONLY on the [start, end] slice. Warmup bars are still fed to the
    runtime so indicators initialise correctly, but their trades and
    equity curve points are excluded from the metric window — otherwise
    OOS Sharpe gets contaminated by the train-window prefix."""
    result, bars = backtest(pine_path, symbol, exchange, timeframe, start, end)
    win_idx = _window_start_idx(bars, start)

    eq_full = list(getattr(result, "equity_curve", []))
    trades_full = list(getattr(result, "trades", []))

    eq = eq_full[win_idx:] if win_idx < len(eq_full) else []
    trades = [t for t in trades_full if getattr(t, "entry_bar", -1) >= win_idx]

    initial = float(eq[0]) if eq else float(getattr(result, "initial_capital", 100000.0))
    net = sum(getattr(t, "pnl", 0.0) for t in trades)
    n = len(trades)
    wins = sum(1 for t in trades if getattr(t, "pnl", 0) > 0)
    return {
        "n_trades": n,
        "net_profit": net,
        "return_pct": (net / initial) if initial > 0 else 0.0,
        "win_rate": (wins / n) if n else 0.0,
        "profit_factor": profit_factor(trades),
        "max_drawdown": max_drawdown(eq),
        "sharpe": sharpe(eq, BARS_PER_YEAR.get(timeframe, 8760)),
        "n_bars": len(eq),
        "n_warmup_bars": win_idx,
        "n_total_bars": len(eq_full),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trial", required=True)
    p.add_argument("--config", default=str(EVAL_ROOT / "test_set.yaml"))
    a = p.parse_args()

    cfg = yaml.safe_load(Path(a.config).read_text())
    trial = json.loads(Path(a.trial).read_text())
    if not trial.get("optimized_pine"):
        trial["holdout"] = {"status": "skipped", "reason": "no optimized.pine"}
        Path(a.trial).write_text(json.dumps(trial, indent=2))
        print(f"[holdout:SKIP] {trial.get('trial_id')} — no optimized.pine")
        return 1

    pine = Path(trial["optimized_pine"])
    if not pine.exists():
        trial["holdout"] = {"status": "skipped", "reason": f"missing {pine}"}
        Path(a.trial).write_text(json.dumps(trial, indent=2))
        print(f"[holdout:SKIP] {trial.get('trial_id')} — file gone")
        return 1

    regime = cfg["regimes"][trial["regime"]]
    train = trial["train_window"]
    holdout = regime["holdout_period"]
    sym = trial.get("symbol") or cfg["defaults"]["symbol"]
    tf = trial.get("timeframe") or cfg["defaults"]["timeframe"]
    ex = trial.get("exchange") or cfg["defaults"]["exchange"]

    print(f"[holdout] IS  {train['start']}..{train['end']}  {pine.name}")
    is_m = evaluate(pine, sym, ex, tf, str(train["start"]), str(train["end"]))
    print(f"[holdout] OOS {holdout['start']}..{holdout['end']}  {pine.name}")
    oos_m = evaluate(pine, sym, ex, tf, str(holdout["start"]), str(holdout["end"]))

    pf_ratio = (oos_m["profit_factor"] / is_m["profit_factor"]) if is_m["profit_factor"] > 0 else None

    trial["holdout"] = {
        "status": "ok",
        "is": is_m,
        "oos": oos_m,
        "delta_sharpe": oos_m["sharpe"] - is_m["sharpe"],
        "is_to_oos_pf_ratio": pf_ratio,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }
    Path(a.trial).write_text(json.dumps(trial, indent=2))
    print(f"[holdout:OK] {trial['trial_id']}  IS Sharpe={is_m['sharpe']:.2f}  OOS Sharpe={oos_m['sharpe']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

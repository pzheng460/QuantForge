"""
Generalized Paper Trading Validation System.

Automatically collects results from all data sources, compares against
backtest benchmarks, detects critical issues, and generates alerts.

Multi-phase validation:
    Phase 1 (Day 1-3):   Connectivity
    Phase 2 (Week 1-2):  Behavior
    Phase 3 (Month 1+):  Preliminary performance
    Phase 4 (Month 3+):  Statistical validation

Strategy-specific callers pass:
    - base_dir: directory containing live_performance.json, backtest_results.json, etc.
    - log_file_name: strategy log file name (e.g. "hurst_kalman.log")
    - table_prefix: SQLite table name prefix
    - annual_trades_estimate: expected trades/year for the chosen config
    - recommended_days: minimum test duration recommendation
"""

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------
DEFAULT_RECOMMENDED_DAYS = 90
DEFAULT_ANNUAL_TRADES_ESTIMATE = 13
DEFAULT_TABLE_PREFIX = "strategy"


def _find_cache_db(base_dir: Path) -> Path:
    """Locate the cache.db file relative to the project root."""
    # Walk up to find .keys/cache.db
    candidate = base_dir
    for _ in range(10):
        db = candidate / ".keys" / "cache.db"
        if db.exists():
            return db
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return base_dir / ".keys" / "cache.db"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Alert:
    severity: str  # CRITICAL / WARNING / INFO
    category: str  # Risk / Performance / Execution / Configuration / Duration
    title: str
    detail: str
    actual: str
    expected: str
    action: str


@dataclass
class ValidationReport:
    timestamp: str
    mesa_index: int
    config_name: str
    duration_days: float
    phase: str
    confidence: str
    alerts: list[Alert]
    paper_metrics: dict[str, Any]
    backtest_benchmark: dict[str, Any]
    trade_analysis: dict[str, Any]
    execution: dict[str, Any]
    recommendation: str  # CONTINUE / INVESTIGATE / STOP / READY


# ---------------------------------------------------------------------------
# DataLoader
# ---------------------------------------------------------------------------
class DataLoader:
    """Load all data sources for validation."""

    def __init__(
        self,
        base_dir: Path,
        log_file_name: str = "strategy.log",
        table_prefix: str = DEFAULT_TABLE_PREFIX,
    ):
        self.base_dir = base_dir
        self._log_file_name = log_file_name
        self._table_prefix = table_prefix
        self._cache_db_path = _find_cache_db(base_dir)

    def load_live_performance(self) -> dict:
        path = self.base_dir / "live_performance.json"
        if not path.exists():
            return {}
        with open(path) as f:
            return json.load(f)

    def load_backtest_benchmark(self, mesa_index: int) -> dict:
        path = self.base_dir / "backtest_results.json"
        if not path.exists():
            return {}
        with open(path) as f:
            results = json.load(f)

        period_priority = ["1y", "6m", "3m", "2_years", "1_year", "6_months"]
        for suffix in period_priority:
            key = f"mesa{mesa_index}_{suffix}"
            if key in results:
                return results[key]
        for suffix in period_priority:
            key = f"{mesa_index}_{suffix}"
            if key in results:
                return results[key]
        return {}

    def parse_log_events(self) -> dict:
        path = self.base_dir / self._log_file_name
        events: dict[str, Any] = {
            "circuit_breaker_count": 0,
            "stop_loss_count": 0,
            "order_failed_count": 0,
            "market_states": {"mean_reverting": 0, "random_walk": 0, "trending": 0},
            "total_bars": 0,
            "warmup_error": False,
        }
        if not path.exists():
            return events

        bar_pattern = re.compile(r"Bar=(\d+).*State=(\w+)")
        with open(path) as f:
            for line in f:
                if "CIRCUIT BREAKER" in line:
                    events["circuit_breaker_count"] += 1
                if (
                    "Stop loss" in line
                    or "STOP LOSS" in line
                    or "stop_loss" in line.lower()
                ):
                    events["stop_loss_count"] += 1
                if "FAILED" in line and "Order" in line:
                    events["order_failed_count"] += 1
                if "Failed to warm up" in line:
                    events["warmup_error"] = True

                m = bar_pattern.search(line)
                if m:
                    bar_num = int(m.group(1))
                    state = m.group(2)
                    if bar_num > events["total_bars"]:
                        events["total_bars"] = bar_num
                    if state in events["market_states"]:
                        events["market_states"][state] += 1

        return events

    def query_order_status(self, mesa_index: int) -> dict:
        result: dict[str, Any] = {
            "total_orders": 0,
            "filled": 0,
            "failed": 0,
            "other": 0,
            "total_fees": 0.0,
            "available": False,
        }
        if not self._cache_db_path.exists():
            return result

        table = f"{self._table_prefix}_orders"

        try:
            conn = sqlite3.connect(str(self._cache_db_path))
            cur = conn.cursor()

            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            )
            if not cur.fetchone():
                conn.close()
                return result

            result["available"] = True

            cur.execute(f"SELECT status, COUNT(*) FROM [{table}] GROUP BY status")  # noqa: S608
            for status, count in cur.fetchall():
                result["total_orders"] += count
                if status == "FILLED":
                    result["filled"] += count
                elif status == "FAILED":
                    result["failed"] += count
                else:
                    result["other"] += count

            cur.execute(f"SELECT fee FROM [{table}] WHERE fee IS NOT NULL")  # noqa: S608
            for (fee_str,) in cur.fetchall():
                try:
                    result["total_fees"] += float(fee_str)
                except (ValueError, TypeError):
                    pass

            conn.close()
        except sqlite3.Error:
            pass

        return result


# ---------------------------------------------------------------------------
# PaperValidator
# ---------------------------------------------------------------------------
class PaperValidator:
    """Core validation logic (strategy-agnostic)."""

    def __init__(
        self,
        mesa_index: int = 0,
        config_name: str = "",
        base_dir: Optional[Path] = None,
        log_file_name: str = "strategy.log",
        table_prefix: str = DEFAULT_TABLE_PREFIX,
        annual_trades_estimate: int = DEFAULT_ANNUAL_TRADES_ESTIMATE,
        recommended_days: int = DEFAULT_RECOMMENDED_DAYS,
    ):
        self.mesa_index = mesa_index
        self.config_name = config_name or f"Mesa #{mesa_index}"
        self._annual_trades_estimate = annual_trades_estimate
        self._recommended_days = recommended_days
        self.loader = DataLoader(
            base_dir=base_dir or Path.cwd(),
            log_file_name=log_file_name,
            table_prefix=table_prefix,
        )

    def validate(self) -> ValidationReport:
        perf = self.loader.load_live_performance()
        benchmark = self.loader.load_backtest_benchmark(self.mesa_index)
        log_events = self.loader.parse_log_events()
        execution = self.loader.query_order_status(self.mesa_index)

        duration_days = 0.0
        if perf.get("start_time") and perf.get("last_update"):
            start = datetime.fromisoformat(perf["start_time"])
            end = datetime.fromisoformat(perf["last_update"])
            duration_days = (end - start).total_seconds() / 86400

        total_trades = perf.get("total_trades", 0)
        phase = self._determine_phase(duration_days)
        confidence = self._determine_confidence(total_trades)

        trades = perf.get("trades", [])
        trade_analysis = self._build_trade_analysis(trades, total_trades)

        alerts: list[Alert] = []
        alerts.extend(self._check_duration(duration_days, total_trades))
        alerts.extend(
            self._check_critical_risks(
                perf, benchmark, log_events, trade_analysis, duration_days
            )
        )
        alerts.extend(self._check_performance(perf, benchmark, total_trades))
        alerts.extend(
            self._check_trade_behavior(perf, trade_analysis, benchmark, duration_days)
        )
        alerts.extend(self._check_execution(execution, log_events))

        recommendation = self._determine_recommendation(
            alerts, total_trades, perf, benchmark
        )

        return ValidationReport(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            mesa_index=self.mesa_index,
            config_name=self.config_name,
            duration_days=round(duration_days, 2),
            phase=phase,
            confidence=confidence,
            alerts=alerts,
            paper_metrics={
                "total_return_pct": perf.get("total_return_pct", 0),
                "max_drawdown_pct": perf.get("max_drawdown_pct", 0),
                "win_rate_pct": perf.get("win_rate_pct", 0),
                "total_trades": total_trades,
                "profit_factor": perf.get("profit_factor", 0),
                "avg_win_pct": perf.get("avg_win_pct", 0),
                "avg_loss_pct": perf.get("avg_loss_pct", 0),
                "current_balance": perf.get("current_balance", 0),
                "initial_balance": perf.get("initial_balance", 0),
            },
            backtest_benchmark={
                "total_return_pct": benchmark.get("total_return_pct", 0),
                "max_drawdown_pct": benchmark.get("max_drawdown_pct", 0),
                "win_rate_pct": benchmark.get("win_rate_pct", 0),
                "total_trades": benchmark.get("total_trades", 0),
                "profit_factor": benchmark.get("profit_factor", 0),
                "period": benchmark.get("period", "N/A"),
            },
            trade_analysis=trade_analysis,
            execution=execution,
            recommendation=recommendation,
        )

    # -- Phase / confidence helpers --
    def _determine_phase(self, duration_days: float) -> str:
        if duration_days <= 3:
            return "Phase 1 — Connectivity Validation"
        if duration_days <= 14:
            return "Phase 2 — Behavior Validation"
        if duration_days <= 30:
            return "Phase 3 — Preliminary Performance"
        return "Phase 4 — Statistical Validation"

    def _determine_confidence(self, total_trades: int) -> str:
        if total_trades < 3:
            return "Very Low"
        if total_trades < 10:
            return "Low"
        if total_trades < 20:
            return "Moderate"
        if total_trades < 30:
            return "High"
        return "Very High"

    # -- Trade analysis --
    def _build_trade_analysis(self, trades: list[dict], total_trades: int) -> dict:
        long_count = sum(1 for t in trades if t.get("side") == "long")
        short_count = sum(1 for t in trades if t.get("side") == "short")

        exit_reasons: dict[str, int] = {}
        for t in trades:
            reason = t.get("exit_reason", "unknown")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        max_consecutive_losses = 0
        current_streak = 0
        for t in trades:
            if t.get("pnl", 0) < 0:
                current_streak += 1
                max_consecutive_losses = max(max_consecutive_losses, current_streak)
            else:
                current_streak = 0

        holding_minutes: list[float] = []
        for t in trades:
            if t.get("entry_time") and t.get("exit_time"):
                try:
                    entry = datetime.fromisoformat(t["entry_time"])
                    exit_ = datetime.fromisoformat(t["exit_time"])
                    holding_minutes.append((exit_ - entry).total_seconds() / 60)
                except (ValueError, TypeError):
                    pass

        avg_holding_min = (
            sum(holding_minutes) / len(holding_minutes) if holding_minutes else 0
        )

        stop_loss_count = sum(
            1
            for r, c in exit_reasons.items()
            for _ in range(c)
            if "stop" in r.lower() or "stop_loss" in r.lower()
        )

        return {
            "total": total_trades,
            "long": long_count,
            "short": short_count,
            "exit_reasons": exit_reasons,
            "stop_loss_exits": stop_loss_count,
            "max_consecutive_losses": max_consecutive_losses,
            "avg_holding_minutes": round(avg_holding_min, 1),
            "holding_minutes_list": holding_minutes,
        }

    # -- Alert checks --
    def _check_duration(self, duration_days: float, total_trades: int) -> list[Alert]:
        alerts: list[Alert] = []
        rec_days = self._recommended_days

        if total_trades < 10:
            alerts.append(
                Alert(
                    severity="WARNING",
                    category="Duration",
                    title="Insufficient trade sample",
                    detail=f"Only {total_trades} trades completed. Need at least 10 for basic statistical significance.",
                    actual=str(total_trades),
                    expected=">=10",
                    action="Continue running paper trading to accumulate more trades.",
                )
            )

        if duration_days < rec_days * 0.1 and total_trades == 0:
            alerts.append(
                Alert(
                    severity="INFO",
                    category="Duration",
                    title="Test just started",
                    detail=f"Running for {duration_days:.1f} days. Recommended minimum: {rec_days} days for {self.config_name}.",
                    actual=f"{duration_days:.1f} days",
                    expected=f"{rec_days} days",
                    action="Continue running. Most metrics are not yet meaningful.",
                )
            )

        return alerts

    def _check_critical_risks(
        self,
        perf: dict,
        benchmark: dict,
        log_events: dict,
        trade_analysis: dict,
        duration_days: float,
    ) -> list[Alert]:
        alerts: list[Alert] = []
        total_trades = perf.get("total_trades", 0)

        bt_dd = abs(benchmark.get("max_drawdown_pct", 0))
        paper_dd = abs(perf.get("max_drawdown_pct", 0))
        if bt_dd > 0 and paper_dd > bt_dd * 2 and total_trades >= 3:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Risk",
                    title="Max drawdown exceeds 2x backtest",
                    detail=f"Paper drawdown {paper_dd:.2f}% vs backtest {bt_dd:.2f}% (threshold: {bt_dd * 2:.2f}%).",
                    actual=f"{paper_dd:.2f}%",
                    expected=f"<{bt_dd * 2:.2f}%",
                    action="Stop immediately. Check risk parameters and market conditions.",
                )
            )

        bt_trades = benchmark.get("total_trades", 0)
        bt_period = benchmark.get("period", "")
        if bt_trades > 0 and duration_days > 7 and total_trades > 0:
            bt_period_days = self._period_to_days(bt_period)
            if bt_period_days > 0:
                bt_annual = bt_trades / (bt_period_days / 365)
                paper_annual = (
                    total_trades / (duration_days / 365) if duration_days > 0 else 0
                )
                if paper_annual > bt_annual * 3:
                    alerts.append(
                        Alert(
                            severity="CRITICAL",
                            category="Risk",
                            title="Abnormally high trade frequency",
                            detail=f"Annualised: paper {paper_annual:.0f} vs backtest {bt_annual:.0f} trades/year.",
                            actual=f"{paper_annual:.0f}/year",
                            expected=f"<{bt_annual * 3:.0f}/year",
                            action="Possible misconfiguration or overfitting. Review entry conditions.",
                        )
                    )

        if total_trades >= 5:
            sl_exits = trade_analysis.get("stop_loss_exits", 0)
            sl_pct = (sl_exits / total_trades * 100) if total_trades > 0 else 0
            if sl_pct > 50:
                alerts.append(
                    Alert(
                        severity="CRITICAL",
                        category="Risk",
                        title="Stop-loss exit ratio too high",
                        detail=f"{sl_exits}/{total_trades} trades ({sl_pct:.1f}%) exited via stop-loss.",
                        actual=f"{sl_pct:.1f}%",
                        expected="<50%",
                        action="Entry conditions may be flawed. Re-tune parameters.",
                    )
                )

        cb_count = log_events.get("circuit_breaker_count", 0)
        if cb_count > 0:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Risk",
                    title="Circuit breaker triggered",
                    detail=f"Circuit breaker activated {cb_count} time(s) — daily loss limit exceeded.",
                    actual=str(cb_count),
                    expected="0",
                    action="Review strategy behavior on those days. Check for cascading losses.",
                )
            )

        max_consec = trade_analysis.get("max_consecutive_losses", 0)
        if max_consec >= 5:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Risk",
                    title="Excessive consecutive losses",
                    detail=f"{max_consec} consecutive losing trades detected.",
                    actual=str(max_consec),
                    expected="<5",
                    action="Strategy may have become ineffective. Consider stopping.",
                )
            )

        annual_est = self._annual_trades_estimate
        expected_interval_days = 365 / annual_est if annual_est > 0 else 365
        if total_trades == 0 and duration_days > expected_interval_days * 3:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Execution",
                    title="Strategy stalled — zero trades",
                    detail=(
                        f"Running for {duration_days:.1f} days with 0 trades. "
                        f"Expected ~1 trade every {expected_interval_days:.0f} days for {self.config_name}."
                    ),
                    actual="0 trades",
                    expected=f">=1 in {expected_interval_days * 3:.0f} days",
                    action="Check warmup, data feed, and signal generation.",
                )
            )

        if total_trades >= 10:
            bt_wr = benchmark.get("win_rate_pct", 0)
            paper_wr = perf.get("win_rate_pct", 0)
            deviation = abs(paper_wr - bt_wr)
            if deviation > 25:
                alerts.append(
                    Alert(
                        severity="CRITICAL",
                        category="Performance",
                        title="Win rate severely deviates from backtest",
                        detail=f"Paper {paper_wr:.1f}% vs backtest {bt_wr:.1f}% (deviation: {deviation:.1f}pp).",
                        actual=f"{paper_wr:.1f}%",
                        expected=f"~{bt_wr:.1f}% (±25pp)",
                        action="Check execution slippage and cost model.",
                    )
                )

        return alerts

    def _check_performance(
        self, perf: dict, benchmark: dict, total_trades: int
    ) -> list[Alert]:
        alerts: list[Alert] = []

        if total_trades < 10:
            return alerts

        bt_wr = benchmark.get("win_rate_pct", 0)
        paper_wr = perf.get("win_rate_pct", 0)
        wr_dev = abs(paper_wr - bt_wr)
        if 15 < wr_dev <= 25:
            alerts.append(
                Alert(
                    severity="WARNING",
                    category="Performance",
                    title="Win rate deviates from backtest",
                    detail=f"Paper {paper_wr:.1f}% vs backtest {bt_wr:.1f}% (deviation: {wr_dev:.1f}pp).",
                    actual=f"{paper_wr:.1f}%",
                    expected=f"~{bt_wr:.1f}% (±15pp)",
                    action="Monitor. Could be market regime difference.",
                )
            )

        bt_pf = benchmark.get("profit_factor", 0)
        paper_pf = perf.get("profit_factor", 0)
        if bt_pf > 0:
            pf_dev = abs(paper_pf - bt_pf) / bt_pf * 100
            if pf_dev > 50:
                alerts.append(
                    Alert(
                        severity="WARNING",
                        category="Performance",
                        title="Profit factor deviates from backtest",
                        detail=f"Paper {paper_pf:.2f} vs backtest {bt_pf:.2f} (deviation: {pf_dev:.1f}%).",
                        actual=f"{paper_pf:.2f}",
                        expected=f"~{bt_pf:.2f} (±50%)",
                        action="Check cost model accuracy and slippage.",
                    )
                )

        return alerts

    def _check_trade_behavior(
        self, perf: dict, trade_analysis: dict, benchmark: dict, duration_days: float
    ) -> list[Alert]:
        alerts: list[Alert] = []
        total_trades = perf.get("total_trades", 0)

        annual_est = self._annual_trades_estimate
        expected_interval = 365 / annual_est if annual_est > 0 else 365
        if total_trades > 0 and duration_days > expected_interval * 2:
            actual_interval = duration_days / total_trades
            if actual_interval > expected_interval * 2:
                alerts.append(
                    Alert(
                        severity="WARNING",
                        category="Performance",
                        title="Trading frequency too low",
                        detail=(
                            f"Average interval: {actual_interval:.1f} days vs "
                            f"expected ~{expected_interval:.1f} days."
                        ),
                        actual=f"{actual_interval:.1f} days/trade",
                        expected=f"~{expected_interval:.1f} days/trade",
                        action="May be normal. Continue monitoring.",
                    )
                )

        avg_hold = trade_analysis.get("avg_holding_minutes", 0)
        exp_hold = 120
        if avg_hold > exp_hold * 5 and total_trades >= 3:
            alerts.append(
                Alert(
                    severity="WARNING",
                    category="Performance",
                    title="Abnormally long holding time",
                    detail=f"Average holding: {avg_hold:.0f} min vs expected ~{exp_hold} min.",
                    actual=f"{avg_hold:.0f} min",
                    expected=f"~{exp_hold} min",
                    action="Check exit signal generation. Positions may be getting stuck.",
                )
            )

        if total_trades >= 3:
            long_pct = (
                trade_analysis["long"] / total_trades * 100 if total_trades > 0 else 0
            )
            short_pct = (
                trade_analysis["short"] / total_trades * 100 if total_trades > 0 else 0
            )
            exit_reasons = trade_analysis.get("exit_reasons", {})
            reason_str = ", ".join(f"{k}: {v}" for k, v in exit_reasons.items())
            alerts.append(
                Alert(
                    severity="INFO",
                    category="Performance",
                    title="Trade distribution",
                    detail=(
                        f"Long: {trade_analysis['long']} ({long_pct:.0f}%), "
                        f"Short: {trade_analysis['short']} ({short_pct:.0f}%). "
                        f"Exit reasons: {reason_str}"
                    ),
                    actual=f"L:{trade_analysis['long']} S:{trade_analysis['short']}",
                    expected="balanced",
                    action="For reference only.",
                )
            )

        return alerts

    def _check_execution(self, execution: dict, log_events: dict) -> list[Alert]:
        alerts: list[Alert] = []

        if not execution.get("available"):
            alerts.append(
                Alert(
                    severity="INFO",
                    category="Execution",
                    title="Order database not available",
                    detail="Could not access cache.db order history.",
                    actual="N/A",
                    expected="accessible",
                    action="Check if cache.db exists in .keys/ directory.",
                )
            )
            return alerts

        failed = execution.get("failed", 0)
        if failed > 0:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Execution",
                    title="Failed orders detected",
                    detail=f"{failed} order(s) with FAILED status in cache.db.",
                    actual=str(failed),
                    expected="0",
                    action="Check API connection, order parameters, and account balance.",
                )
            )

        log_failures = log_events.get("order_failed_count", 0)
        if log_failures > 0:
            alerts.append(
                Alert(
                    severity="CRITICAL",
                    category="Execution",
                    title="Order failures in log",
                    detail=f"{log_failures} order failure(s) detected in log file.",
                    actual=str(log_failures),
                    expected="0",
                    action="Review log file for detailed error messages.",
                )
            )

        return alerts

    # -- Recommendation --
    def _determine_recommendation(
        self, alerts: list[Alert], total_trades: int, perf: dict, benchmark: dict
    ) -> str:
        has_critical = any(a.severity == "CRITICAL" for a in alerts)
        has_warning = any(a.severity == "WARNING" for a in alerts)

        if has_critical:
            return "STOP"
        if has_warning and total_trades < 10:
            return "INVESTIGATE"
        if total_trades < 10:
            return "CONTINUE"

        bt_wr = benchmark.get("win_rate_pct", 0)
        paper_wr = perf.get("win_rate_pct", 0)
        bt_dd = abs(benchmark.get("max_drawdown_pct", 0))
        paper_dd = abs(perf.get("max_drawdown_pct", 0))

        wr_ok = abs(paper_wr - bt_wr) <= 15
        dd_ok = paper_dd <= bt_dd * 1.5 if bt_dd > 0 else True

        if wr_ok and dd_ok and not has_warning:
            return "READY"
        return "INVESTIGATE"

    # -- Helpers --
    @staticmethod
    def _period_to_days(period: str) -> int:
        mapping = {
            "3m": 90,
            "6m": 180,
            "1y": 365,
            "2y": 730,
            "6_months": 180,
            "1_year": 365,
            "2_years": 730,
        }
        return mapping.get(period, 0)


# ---------------------------------------------------------------------------
# ReportPrinter
# ---------------------------------------------------------------------------
class ReportPrinter:
    """Format terminal output with colours and save JSON."""

    RED = "\033[91m"
    YELLOW = "\033[93m"
    GREEN = "\033[92m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    SEVERITY_COLOUR = {
        "CRITICAL": "\033[91m",
        "WARNING": "\033[93m",
        "INFO": "\033[96m",
    }

    RECOMMENDATION_COLOUR = {
        "STOP": "\033[91m",
        "INVESTIGATE": "\033[93m",
        "CONTINUE": "\033[96m",
        "READY": "\033[92m",
    }

    RECOMMENDATION_TEXT = {
        "STOP": "Immediately stop paper trading. Resolve CRITICAL issues before restarting.",
        "INVESTIGATE": "Potential issues detected. Investigate before continuing.",
        "CONTINUE": "No severe issues. Continue running paper trading to accumulate more samples.",
        "READY": "All metrics within tolerance. Consider moving to live trading.",
    }

    def __init__(self, recommended_days: int = DEFAULT_RECOMMENDED_DAYS):
        self._recommended_days = recommended_days

    def print_report(self, report: ValidationReport) -> None:
        B, R, DM = self.BOLD, self.RESET, self.DIM
        sep = "=" * 80
        thin = "-" * 60

        print(f"\n{sep}")
        print(f" {B}Paper Trading Validation Report{R}")
        print(sep)
        print(f" Timestamp:  {report.timestamp}")
        print(f" Config:     Mesa #{report.mesa_index} ({report.config_name})")
        print(f" Duration:   {report.duration_days} days")
        print(f" Phase:      {report.phase}")
        print(
            f" Confidence: {report.confidence} ({report.paper_metrics.get('total_trades', 0)} trades)"
        )

        self._section("1. Test Duration Assessment")
        rec_days = self._recommended_days
        trades = report.paper_metrics.get("total_trades", 0)
        print(
            f" Tested:         {report.duration_days} days / recommended {rec_days} days"
        )
        print(f" Trade samples:  {trades} / recommended >=10")
        if trades > 0 and report.duration_days > 0:
            rate = trades / report.duration_days
            if rate > 0:
                days_to_10 = max(0, (10 - trades) / rate)
                print(f" Est. to 10:     ~{days_to_10:.0f} days")
        status = "Sufficient" if trades >= 10 else "Insufficient"
        colour = self.GREEN if trades >= 10 else self.YELLOW
        print(f" Status:         {colour}{status}{R}")

        self._section("2. Performance Comparison (Paper vs Backtest)")
        pm = report.paper_metrics
        bm = report.backtest_benchmark
        insufficient = trades < 10

        header = f" {'Metric':<20} {'Paper':>12} {'Backtest':>12} {'Deviation':>12}"
        print(header)
        print(f" {thin}")
        self._metric_row(
            "Total Return %",
            pm.get("total_return_pct"),
            bm.get("total_return_pct"),
            insufficient,
            suffix="%",
        )
        self._metric_row(
            "Max Drawdown %",
            pm.get("max_drawdown_pct"),
            bm.get("max_drawdown_pct"),
            insufficient,
            suffix="%",
        )
        self._metric_row(
            "Win Rate %",
            pm.get("win_rate_pct"),
            bm.get("win_rate_pct"),
            insufficient,
            suffix="%",
        )
        self._metric_row(
            "Total Trades", pm.get("total_trades"), bm.get("total_trades"), insufficient
        )
        self._metric_row(
            "Profit Factor",
            pm.get("profit_factor"),
            bm.get("profit_factor"),
            insufficient,
        )

        self._section("3. Trade Details")
        ta = report.trade_analysis
        print(
            f" Total trades: {ta['total']} (Long: {ta['long']}, Short: {ta['short']})"
        )
        print(f" Stop-loss exits: {ta['stop_loss_exits']}")
        if ta.get("exit_reasons"):
            print(" Exit reasons:")
            for reason, count in ta["exit_reasons"].items():
                pct = count / ta["total"] * 100 if ta["total"] > 0 else 0
                print(f"   - {reason}: {count} ({pct:.1f}%)")
        if ta.get("avg_holding_minutes", 0) > 0:
            print(f" Avg holding time: {ta['avg_holding_minutes']:.0f} min")
        if ta.get("max_consecutive_losses", 0) > 0:
            print(f" Max consecutive losses: {ta['max_consecutive_losses']}")

        self._section("4. Execution Quality")
        ex = report.execution
        if ex.get("available"):
            print(f" Total orders:  {ex['total_orders']}")
            print(
                f" Filled:        {ex['filled']} ({ex['filled'] / ex['total_orders'] * 100:.0f}%)"
                if ex["total_orders"] > 0
                else " Filled:        0"
            )
            print(f" Failed:        {ex['failed']}")
            print(f" Total fees:    {ex['total_fees']:.4f} USDT")
            status_colour = self.GREEN if ex["failed"] == 0 else self.RED
            status_text = "Normal" if ex["failed"] == 0 else "ISSUES DETECTED"
            print(f" Status:        {status_colour}{status_text}{R}")
        else:
            print(f" {DM}Order database not available{R}")

        self._section("5. Alerts")
        criticals = [a for a in report.alerts if a.severity == "CRITICAL"]
        warnings = [a for a in report.alerts if a.severity == "WARNING"]
        infos = [a for a in report.alerts if a.severity == "INFO"]

        if not criticals:
            print(f" {self.GREEN}[*] CRITICAL: None{R}")
        else:
            for a in criticals:
                print(f" {self.RED}[!] CRITICAL: {a.title}{R}")
                print(f"     {a.detail}")
                print(f"     Actual: {a.actual}  |  Expected: {a.expected}")
                print(f"     Action: {a.action}")
                print()

        if not warnings:
            print(f" {self.GREEN}[*] WARNING: None{R}")
        else:
            for a in warnings:
                print(f" {self.YELLOW}[!] WARNING: {a.title}{R}")
                print(f"     {a.detail}")
                print(f"     Action: {a.action}")
                print()

        if infos:
            for a in infos:
                print(f" {self.CYAN}[i] {a.title}{R}: {a.detail}")

        self._section("6. Recommendation: " + report.recommendation)
        colour = self.RECOMMENDATION_COLOUR.get(report.recommendation, "")
        text = self.RECOMMENDATION_TEXT.get(report.recommendation, "")
        print(f" {colour}{B}{report.recommendation}{R}")
        print(f" {text}")

        if trades < 10 and trades > 0 and report.duration_days > 0:
            rate = trades / report.duration_days
            days_remaining = (10 - trades) / rate if rate > 0 else 0
            print(
                f"\n {DM}Next milestone: 10 trades, estimated in ~{days_remaining:.0f} days{R}"
            )
        elif trades < 10:
            print(f"\n {DM}Waiting for first trades to estimate milestones.{R}")

        print(f"\n{sep}\n")

    def save_json(self, report: ValidationReport, path: Path) -> None:
        data = asdict(report)
        if "holding_minutes_list" in data.get("trade_analysis", {}):
            del data["trade_analysis"]["holding_minutes_list"]
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        print(f" Report saved to: {path}")

    def _section(self, title: str) -> None:
        bar = "\u2501" * 60
        print(f"\n{bar}")
        print(f" {self.BOLD}{title}{self.RESET}")
        print(bar)

    def _metric_row(
        self,
        name: str,
        paper_val: float | None,
        bt_val: float | None,
        insufficient: bool,
        suffix: str = "",
    ) -> None:
        pv = paper_val if paper_val is not None else 0
        bv = bt_val if bt_val is not None else 0

        p_str = f"{pv:.2f}{suffix}"
        b_str = f"{bv:.2f}{suffix}"

        if insufficient:
            dev_str = f"{self.DIM}(insufficient){self.RESET}"
        elif bv != 0:
            dev = ((pv - bv) / abs(bv)) * 100
            colour = self.GREEN if dev >= 0 else self.RED
            dev_str = f"{colour}{dev:+.1f}%{self.RESET}"
        else:
            dev_str = "N/A"

        print(f" {name:<20} {p_str:>12} {b_str:>12} {dev_str:>24}")

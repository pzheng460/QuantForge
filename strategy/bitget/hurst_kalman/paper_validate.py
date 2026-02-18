"""
Hurst-Kalman Paper Trading Validation — thin wrapper over common module.

Usage:
    uv run python strategy/bitget/hurst_kalman/paper_validate.py
    uv run python strategy/bitget/hurst_kalman/paper_validate.py --mesa 0
"""

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_script_dir_str = str(_SCRIPT_DIR)
if _script_dir_str in sys.path:
    sys.path.remove(_script_dir_str)

from strategy.bitget.common.paper_validate import (  # noqa: E402, F401
    Alert,
    DataLoader,
    PaperValidator,
    ReportPrinter,
    ValidationReport,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hurst-Kalman paper trading validation",
    )
    parser.add_argument(
        "--mesa",
        type=int,
        default=None,
        help="Mesa index (0=best). Auto-detected from live_performance.json if omitted.",
    )
    args = parser.parse_args()

    # Auto-detect mesa index from live_performance.json
    mesa_index = args.mesa
    config_name = ""
    if mesa_index is None:
        perf_path = _SCRIPT_DIR / "live_performance.json"
        if perf_path.exists():
            with open(perf_path) as f:
                perf_data = json.load(f)
            mesa_index = perf_data.get("mesa_index", 0)
            config_name = perf_data.get("config_name", "")
            print(f"Auto-detected: Mesa #{mesa_index} ({config_name})")
        else:
            mesa_index = 0
            print(f"No live_performance.json found. Defaulting to Mesa #{mesa_index}.")

    validator = PaperValidator(
        mesa_index=mesa_index,
        config_name=config_name,
        base_dir=_SCRIPT_DIR,
        log_file_name="hurst_kalman.log",
        table_prefix="hurst_kalman",
        annual_trades_estimate=13,
        recommended_days=90,
    )
    report = validator.validate()

    printer = ReportPrinter(recommended_days=90)
    printer.print_report(report)

    output_path = _SCRIPT_DIR / "paper_validation_result.json"
    printer.save_json(report, output_path)


if __name__ == "__main__":
    main()

from __future__ import annotations

from datetime import date, timedelta

import pytest

from quantforge.options.engine import OptionReportStore, OptionsEventEngine
from quantforge.options.manager import OptionCandidate, OptionManagerInput
from quantforge.strategies.tsla_nvda_options import (
    TslaNvdaOptionsConfig,
    TslaNvdaOptionsManager,
)


@pytest.mark.critical
def test_options_event_engine_produces_auditable_daily_report(tmp_path):
    today = date(2026, 7, 25)
    strategy = TslaNvdaOptionsManager(TslaNvdaOptionsConfig())
    engine = OptionsEventEngine(strategy)
    report = engine.analyze(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=today + timedelta(days=90),
            earnings_confirmed=True,
            candidates=(
                OptionCandidate(
                    "TSLA_CALL",
                    400,
                    today + timedelta(days=30),
                    5,
                    5.2,
                    0.2,
                    1000,
                    100,
                ),
            ),
        )
    )

    assert report.strategy == "tsla_nvda_options"
    assert report.ticker == "TSLA"
    assert report.action == "开 Covered Call"
    assert report.data_quality == "live_market_data"
    assert report.generated_at

    path = OptionReportStore(tmp_path).save(report)
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert '"ticker": "TSLA"' in path.read_text()

from __future__ import annotations

from datetime import date, timedelta

from quantforge.options.manager import (
    OptionCandidate,
    OptionManager,
    OptionManagerInput,
    ShortCallPosition,
)


def test_manager_will_not_open_call_across_earnings():
    today = date(2026, 7, 23)
    candidate = OptionCandidate(
        symbol="TSLA_CALL",
        strike=400,
        expiration=today + timedelta(days=30),
        bid=5,
        ask=5.2,
        delta=0.18,
        open_interest=1000,
        volume=100,
    )

    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=today + timedelta(days=20),
            earnings_confirmed=True,
            candidates=(candidate,),
        )
    )

    assert decision.action == "不操作"
    assert "财报" in decision.reasons[0]


def test_manager_selects_liquid_delta_fit_covered_call():
    today = date(2026, 7, 23)
    candidates = (
        OptionCandidate(
            "wide", 380, today + timedelta(days=30), 1, 2, 0.18, 100, 10
        ),
        OptionCandidate(
            "fit", 390, today + timedelta(days=30), 4.9, 5, 0.19, 1000, 100
        ),
    )

    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=300,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=350,
            trend_state="横盘",
            earnings_date=today + timedelta(days=90),
            earnings_confirmed=True,
            candidates=candidates,
        )
    )

    assert decision.action == "开 Covered Call"
    assert decision.contract_symbol == "fit"
    assert decision.contracts == 1


def test_existing_short_call_profit_take_precedes_new_entry():
    today = date(2026, 7, 23)
    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="NVDA",
            as_of=today,
            shares=100,
            minimum_core_shares=0,
            maximum_covered_ratio=1,
            stock_price=170,
            trend_state="温和上涨",
            short_calls=(
                ShortCallPosition(
                    "NVDA_CALL",
                    strike=190,
                    expiration=today + timedelta(days=14),
                    contracts=1,
                    entry_credit=5,
                    ask=1.4,
                    delta=0.2,
                ),
            ),
        )
    )

    assert decision.action == "平仓短 Call"
    assert decision.contract_symbol == "NVDA_CALL"


def test_delta_trigger_rolls_to_viable_replacement():
    """Delta reaching the dynamic trigger should ROLL (atomic close+reopen)
    when a viable replacement exists, instead of closing-and-holding."""
    today = date(2026, 7, 23)
    replacement = OptionCandidate(
        symbol="TSLA_CALL_NEW",
        strike=400,
        expiration=today + timedelta(days=35),
        bid=6,
        ask=6.2,
        delta=0.19,
        open_interest=5000,
        volume=300,
    )
    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=360,
            trend_state="横盘",
            earnings_date=today + timedelta(days=90),
            earnings_confirmed=True,
            candidates=(replacement,),
            short_calls=(
                ShortCallPosition(
                    symbol="TSLA_CALL_OLD",
                    strike=340,
                    expiration=today + timedelta(days=10),
                    contracts=1,
                    entry_credit=8,
                    ask=6.0,  # profit (8-6)/8=25% — below profit_take
                    delta=0.55,
                ),
            ),
        )
    )
    assert decision.action == "滚动 Covered Call"
    assert decision.contract_symbol == "TSLA_CALL_OLD"
    assert decision.contracts == 1
    assert decision.roll_to_symbol == "TSLA_CALL_NEW"
    assert decision.roll_to_price == 6


def test_delta_trigger_without_replacement_closes_and_holds():
    today = date(2026, 7, 23)
    decision = OptionManager().evaluate(
        OptionManagerInput(
            ticker="TSLA",
            as_of=today,
            shares=200,
            minimum_core_shares=100,
            maximum_covered_ratio=0.5,
            stock_price=360,
            trend_state="横盘",
            earnings_date=today + timedelta(days=90),
            earnings_confirmed=True,
            candidates=(),  # no viable replacement
            short_calls=(
                ShortCallPosition(
                    symbol="TSLA_CALL_OLD",
                    strike=340,
                    expiration=today + timedelta(days=10),
                    contracts=1,
                    entry_credit=8,
                    ask=6.0,  # below profit_take
                    delta=0.55,
                ),
            ),
        )
    )
    assert decision.action == "买回后暂不重开"
    assert decision.roll_to_symbol is None

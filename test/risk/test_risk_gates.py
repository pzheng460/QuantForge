"""Remaining real-money risk gates: option nakedness, quote freshness/spread,
leverage caps (global + instrument), order notional (incl. option multiplier),
max option legs, and daily-entry rollback."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    CryptoPerpetual,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import (
    MultiLegOrderIntent,
    OrderIntent,
    OrderSide,
)
from quantforge.portfolio.ledger import PortfolioLedger, Position
from quantforge.risk.engine import RiskEngine, RiskLimits, RiskRejected

_NVDA = InstrumentId("NVDA", AssetClass.EQUITY, "schwab")
_TSLA = InstrumentId("TSLA", AssetClass.EQUITY, "schwab")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _equity() -> Equity:
    return Equity(_TSLA)


def _nvda_equity() -> Equity:
    return Equity(_NVDA)


def _options(side: OrderSide, right: OptionRight, qty: float = 1):
    return EquityOption(
        id=InstrumentId(
            "NVDA  260821C00200000", AssetClass.EQUITY_OPTION, "schwab"
        ),
        underlying=_NVDA,
        expiration=date(2026, 8, 21),
        strike=200.0,
        right=right,
    )


def _intent(
    *,
    instrument=None,
    side=OrderSide.BUY,
    quantity: float = 1,
    reduce_only: bool = False,
    leverage: float = 1,
    quote=None,
    limit_price: float | None = None,
) -> OrderIntent:
    instrument = instrument or _equity()
    return OrderIntent(
        strategy_id="test",
        instrument=instrument,
        side=side,
        quantity=quantity,
        reduce_only=reduce_only,
        leverage=leverage,
        quote_bid=quote[0] if quote else None,
        quote_ask=quote[1] if quote else None,
        quote_timestamp=quote[2] if quote else None,
        limit_price=limit_price,
    )


def _engine(limits: RiskLimits | None = None) -> RiskEngine:
    return RiskEngine(
        limits or RiskLimits(live_enabled=True, max_daily_new_positions=10)
    )


def _ledger(*positions) -> PortfolioLedger:
    ledger = PortfolioLedger(cash={"USD": 1_000_000})
    for instrument, qty, price in positions:
        ledger.positions[instrument.id] = Position(instrument, qty, price)
    return ledger


# ─── Option nakedness ────────────────────────────────────────────────────────

def test_naked_call_is_rejected():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(intent, _ledger())


def test_short_call_covered_by_shares_is_authorized():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    ledger = _ledger((_nvda_equity(), 100, 200))  # 1 call × 100 multiplier covered
    _engine().authorize(intent, ledger)


def test_short_call_covered_by_one_share_is_rejected():
    intent = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    ledger = _ledger((_nvda_equity(), 1, 200))
    with pytest.raises(RiskRejected, match="naked call"):
        _engine().authorize(intent, ledger)


def test_short_call_covered_by_long_call_is_authorized():
    short = _intent(instrument=_options(OrderSide.SELL, OptionRight.CALL), side=OrderSide.SELL)
    long_leg = _intent(instrument=_options(OrderSide.BUY, OptionRight.CALL), side=OrderSide.BUY)
    plan = MultiLegOrderIntent(
        strategy_id="test", net_limit_price=-2.5, legs=(long_leg, short)
    )
    _engine().authorize(plan, _ledger())


def test_uncovered_short_put_is_rejected_without_cash():
    put = _options(OrderSide.SELL, OptionRight.PUT)  # strike 200 × 100 = 20_000
    intent = _intent(instrument=put, side=OrderSide.SELL)
    ledger = _ledger()
    ledger.cash["USD"] = 5_000
    with pytest.raises(RiskRejected, match="uncovered short put"):
        _engine().authorize(intent, ledger)


def test_uncovered_short_put_authorized_with_sufficient_cash():
    put = _options(OrderSide.SELL, OptionRight.PUT)
    intent = _intent(instrument=put, side=OrderSide.SELL)
    ledger = _ledger()
    ledger.cash["USD"] = 50_000
    _engine().authorize(intent, ledger)


def test_short_put_covered_by_long_put_is_authorized():
    short = _intent(instrument=_options(OrderSide.SELL, OptionRight.PUT), side=OrderSide.SELL)
    long_leg = _intent(instrument=_options(OrderSide.BUY, OptionRight.PUT), side=OrderSide.BUY)
    plan = MultiLegOrderIntent(
        strategy_id="test", net_limit_price=-2.5, legs=(long_leg, short)
    )
    _engine().authorize(plan, _ledger())


# ─── Quote freshness and spread ──────────────────────────────────────────────

def test_stale_quote_is_rejected():
    old = _now() - timedelta(hours=1)
    intent = _intent(side=OrderSide.BUY, quote=(100, 101, old))
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="stale"):
        engine.authorize(intent, _ledger())


def test_future_quote_is_rejected():
    future = _now() + timedelta(minutes=5)
    intent = _intent(side=OrderSide.BUY, quote=(100, 101, future))
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="stale"):
        engine.authorize(intent, _ledger())


def test_missing_quote_is_rejected():
    intent = _intent(side=OrderSide.BUY, quote=None)
    engine = _engine(RiskLimits(live_enabled=True, require_fresh_quote=True))
    with pytest.raises(RiskRejected, match="fresh quote"):
        engine.authorize(intent, _ledger())


def test_wide_spread_is_rejected():
    # (102 - 98) / 100 = 4% > default 15%? No — set a tight limit instead.
    intent = _intent(side=OrderSide.BUY, quote=(99, 101, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_spread_pct=0.01))
    with pytest.raises(RiskRejected, match="spread limit"):
        engine.authorize(intent, _ledger())


# ─── Leverage caps ───────────────────────────────────────────────────────────

def test_global_leverage_cap_is_enforced():
    intent = _intent(side=OrderSide.BUY, leverage=5)
    engine = _engine(RiskLimits(live_enabled=True, max_leverage=3))
    with pytest.raises(RiskRejected, match="maximum leverage"):
        engine.authorize(intent, _ledger())


def test_instrument_leverage_cap_is_enforced():
    perp = CryptoPerpetual(
        id=InstrumentId("BTC/USDT:USDT", AssetClass.CRYPTO_PERPETUAL, "okx"),
        max_leverage=2,
    )
    intent = _intent(instrument=perp, side=OrderSide.BUY, leverage=3)
    with pytest.raises(RiskRejected, match="instrument leverage"):
        _engine().authorize(intent, _ledger())


# ─── Notional limits (incl. option multiplier) ───────────────────────────────

def test_notional_cap_uses_quote_ask():
    intent = _intent(side=OrderSide.BUY, quantity=1000, quote=(100, 100, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_order_notional=10_000))
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, _ledger())


def test_option_notional_applies_multiplier():
    # 1 option × 200 strike-style premium × 100 multiplier = 20_000 > 10_000
    put = _options(OrderSide.BUY, OptionRight.PUT)
    intent = _intent(instrument=put, side=OrderSide.BUY, quote=(250, 250, _now()))
    engine = _engine(RiskLimits(live_enabled=True, max_order_notional=10_000))
    with pytest.raises(RiskRejected, match="notional"):
        engine.authorize(intent, _ledger())


# ─── Max option legs ─────────────────────────────────────────────────────────

def test_too_many_option_legs_is_rejected():
    legs = tuple(
        _intent(
            instrument=_options(OrderSide.BUY, OptionRight.CALL),
            side=OrderSide.BUY,
        )
        for _ in range(5)
    )
    plan = MultiLegOrderIntent(strategy_id="test", net_limit_price=1.0, legs=legs)
    engine = _engine(RiskLimits(live_enabled=True, max_option_legs=4))
    with pytest.raises(RiskRejected, match="too many option legs"):
        engine.authorize(plan, _ledger())


# ─── Daily new-position limit + rollback ─────────────────────────────────────

def test_daily_new_position_limit_exceeded_rolls_back():
    engine = _engine(RiskLimits(live_enabled=True, max_daily_new_positions=2))
    day = datetime.now(timezone.utc).date().isoformat()

    intent1 = _intent(side=OrderSide.BUY)
    intent2 = _intent(side=OrderSide.BUY)
    intent3 = _intent(side=OrderSide.BUY)

    engine.authorize(intent1, _ledger())
    engine.authorize(intent2, _ledger())
    with pytest.raises(RiskRejected, match="daily new-position limit"):
        engine.authorize(intent3, _ledger())

    # The failed reservation was rolled back: only 2 openings remain used.
    assert engine._local_entries.get(day) == 2
    # A retry is STILL rejected — the reservation is released, yes, but the
    # cap is a hard ceiling (2 openings), not 3.
    with pytest.raises(RiskRejected, match="daily new-position limit"):
        engine.authorize(intent3, _ledger())
    assert engine._local_entries.get(day) == 2


def test_released_definitive_rejection_frees_daily_count():
    engine = _engine(RiskLimits(live_enabled=True, max_daily_new_positions=1))
    day = datetime.now(timezone.utc).date().isoformat()

    intent = _intent(side=OrderSide.BUY)
    engine.authorize(intent, _ledger())
    engine.release(intent)

    assert engine._local_entries.get(day, 0) == 0
    replacement = _intent(side=OrderSide.BUY)
    engine.authorize(replacement, _ledger())

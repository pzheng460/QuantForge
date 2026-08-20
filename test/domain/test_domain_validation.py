"""Domain objects validate eagerly at construction: garbage must never travel
through the strategy -> intent -> risk boundary as a silently-typed value."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from quantforge.domain.instruments import (
    AssetClass,
    CryptoDerivative,
    CryptoFuture,
    Equity,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide


def _equity() -> Equity:
    return Equity(id=InstrumentId("NVDA", AssetClass.EQUITY, "SCHWAB"))


def _call() -> EquityOption:
    return EquityOption(
        id=InstrumentId("NVDA_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
        underlying=_equity().id,
        expiration=date(2026, 8, 21),
        strike=200,
        right=OptionRight.CALL,
    )


def _intent(**overrides) -> OrderIntent:
    values = dict(
        strategy_id="alpha",
        instrument=_call(),
        side=OrderSide.SELL,
        quantity=1,
        quote_bid=1,
        quote_ask=2,
        quote_timestamp=datetime.now(timezone.utc),
    )
    values.update(overrides)
    return OrderIntent(**values)


def test_order_intent_rejects_non_positive_quantity():
    with pytest.raises(ValueError, match="quantity"):
        _intent(quantity=0)
    with pytest.raises(ValueError, match="quantity"):
        _intent(quantity=-5)


def test_order_intent_rejects_bad_leverage():
    with pytest.raises(ValueError, match="leverage"):
        _intent(leverage=0)
    with pytest.raises(ValueError, match="leverage"):
        _intent(leverage=-2)


def test_order_intent_rejects_inverted_quote():
    with pytest.raises(ValueError, match="quote_ask"):
        _intent(quote_bid=3, quote_ask=2)


def test_order_intent_rejects_naive_quote_timestamp():
    now = datetime.now()
    assert now.tzinfo is None
    with pytest.raises(ValueError, match="quote_timestamp"):
        _intent(quote_timestamp=now)


def test_order_intent_rejects_empty_strategy_or_instrument():
    with pytest.raises(ValueError, match="strategy_id"):
        _intent(strategy_id="  ")
    with pytest.raises(ValueError, match="instrument"):
        _intent(instrument="BTC/USDT:USDT")  # type: ignore[arg-type]


def test_equity_option_rejects_zero_strike_or_unset_expiration():
    with pytest.raises(ValueError, match="strike"):
        EquityOption(
            id=InstrumentId("NVDA_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
            expiration=date(2026, 8, 21),
            strike=0,
        )
    with pytest.raises(ValueError, match="expiration"):
        EquityOption(
            id=InstrumentId("NVDA_CALL", AssetClass.EQUITY_OPTION, "SCHWAB"),
            expiration=date.max,
            strike=200,
        )


def test_derivative_rejects_non_positive_contract_size_or_leverage():
    with pytest.raises(ValueError, match="contract size"):
        CryptoDerivative(id=InstrumentId("X", AssetClass.CRYPTO_PERPETUAL, "v"), contract_size=0)
    with pytest.raises(ValueError, match="leverage"):
        CryptoDerivative(
            id=InstrumentId("X", AssetClass.CRYPTO_PERPETUAL, "v"), max_leverage=-1
        )


def test_multileg_requires_consistent_legs():
    a = _intent()
    b = _intent(strategy_id="bravo")
    with pytest.raises(ValueError, match="at least one leg"):
        MultiLegOrderIntent(strategy_id="alpha", legs=())
    with pytest.raises(ValueError, match="share"):
        MultiLegOrderIntent(strategy_id="alpha", legs=(a, b))


def test_crypto_future_symbol_validates_suffix_and_settlement():
    with pytest.raises(ValueError, match="YYMMDD"):
        CryptoFuture.from_symbol(
            "BTC/USDT:USDT-2609",
            venue="bitget",
            expiration=date(2026, 9, 27),
        )
    with pytest.raises(ValueError, match="settlement"):
        CryptoFuture.from_symbol(
            "BTC/USDT:-260927",
            venue="bitget",
            expiration=date(2026, 9, 27),
        )
    ok = CryptoFuture.from_symbol(
        "BTC/USDT:USDT-260927",
        venue="bitget",
        expiration=date(2026, 9, 27),
        contract_size=0.001,
        max_leverage=25,
    )
    assert ok.settlement_currency == "USDT"
    assert ok.expiration == date(2026, 9, 27)

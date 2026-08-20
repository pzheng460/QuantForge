from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from quantforge.domain.instruments import (
    AssetClass,
    EquityOption,
    InstrumentId,
    OptionRight,
)
from quantforge.domain.intents import MultiLegOrderIntent, OrderIntent, OrderSide
from quantforge.portfolio.ledger import PortfolioLedger
from quantforge.risk.control import GlobalRiskControl

logger = logging.getLogger(__name__)


class RiskRejected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RiskLimits:
    #: Note: ``require_fresh_quote`` defaults to False because paper/demo
    #: engines legitimately trade off bar-close estimates with no market
    #: timestamp. Every REAL-MONEY path must set it True explicitly — the risk
    #: engine warns loudly at construction when a live-enabled engine runs
    #: without quote freshness so a new integration path cannot silently
    #: disable it (see ``RiskEngine.__init__``).
    live_enabled: bool = False
    halted: bool = False
    max_order_notional: float = 10_000
    max_spread_pct: float = 0.15
    max_option_legs: int = 4
    max_quote_age_seconds: float = 30
    require_fresh_quote: bool = False
    max_leverage: float = 3
    max_daily_new_positions: int = 10


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    intent_id: str


class DailyEntryCounter:
    """Process-shared, optionally file-backed counter of daily new positions.

    All live engines in one process share a single instance so the daily
    new-position limit is enforced globally, not per engine; a restart
    reloads the persisted count so the limit cannot be bypassed by redeploying.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._entries: dict[str, int] = {}
        self._lock = threading.Lock()
        if self.path is not None:
            self._load()

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            logger.error(
                "Daily new-position counter unreadable at %s; starting over",
                self.path,
            )
            self._entries = {}
            return
        try:
            self._entries = {
                str(day): int(count)
                for day, count in payload.items()
                if int(count) > 0
            }
        except (TypeError, ValueError):
            logger.error("Daily new-position counter malformed; starting over")
            self._entries = {}

    def _persist(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = self.path.with_suffix(".tmp")
            # 0600 from creation so the file is never world/group-readable
            # (not even for the instant before os.replace + chmod).
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._entries, handle, indent=2)
                handle.write("\n")
            os.replace(tmp, self.path)
        except OSError:
            logger.error("Unable to persist daily new-position counter")

    def reserve(self, opening: int) -> tuple[str, int]:
        """Atomically reserve ``opening`` new positions for today; return (day, used)."""
        with self._lock:
            day = self._day()
            used = self._entries.get(day, 0)
            self._entries[day] = used + opening
            self._persist()
            return day, used + opening

    def release(self, opening: int) -> None:
        """Return a previously reserved count for today (best-effort floor at zero)."""
        with self._lock:
            day = self._day()
            self._entries[day] = max(0, self._entries.get(day, 0) - opening)
            self._persist()


class RiskEngine:
    #: Upper bound on retained, already-authorized intent ids. Ids live while a
    #: submission could still plausibly be in flight; beyond this the oldest
    #: ids are forgotten. A forgotten id can never be replayed as new, because
    #: a fresh id is minted per submission attempt, so this only bounds memory.
    MAX_AUTHORIZED_IDS = 1000

    def __init__(
        self,
        limits: RiskLimits,
        *,
        global_control: GlobalRiskControl | None = None,
        daily_entries: DailyEntryCounter | None = None,
    ) -> None:
        self.limits = limits
        self.global_control = global_control
        # ``_daily_entries`` is the legacy per-engine fallback for tests and
        # standalone use; real-money paths pass a shared DailyEntryCounter.
        self.daily_entries = daily_entries
        self._local_entries: dict[str, int] = {}
        # Ordered set of in-flight intent ids (dict preserves insertion order):
        # membership for duplicate protection, insertion order for pruning.
        self._authorized: dict[str, None] = {}
        if limits.live_enabled and not limits.require_fresh_quote:
            # L6: quote freshness must never be silently off on a live-enabled
            # engine. Demo engines legitimately run on bar-close estimates, so
            # this is a loud warning rather than a hard error — but any
            # REAL-MONEY integration path that forgets require_fresh_quote=True
            # now gets an explicit signal instead of silently trading on stale
            # quotes.
            logger.warning(
                "RiskEngine(live_enabled=True) constructed WITHOUT "
                "require_fresh_quote — quote-age and freshness gates are OFF. "
                "Real-money engines must pass require_fresh_quote=True."
            )

    def authorize(
        self, intent: OrderIntent | MultiLegOrderIntent, ledger: PortfolioLedger
    ) -> RiskDecision:
        if not self.limits.live_enabled:
            raise RiskRejected("live trading is disabled")
        if self.global_control is not None:
            global_state = self.global_control.get()
            if global_state.halted:
                detail = f": {global_state.reason}" if global_state.reason else ""
                raise RiskRejected(f"global risk control is halted{detail}")
        if self.limits.halted:
            raise RiskRejected("risk engine is halted")
        if intent.intent_id in self._authorized:
            raise RiskRejected("duplicate intent")
        legs = intent.legs if isinstance(intent, MultiLegOrderIntent) else (intent,)
        if len(legs) > self.limits.max_option_legs:
            raise RiskRejected("too many option legs")
        for leg in legs:
            self._validate_order(leg)
        # Nakedness is enforced on the whole plan (all legs share one coverage
        # pool), so multiple uncovered legs in one intent cannot bypass it.
        self._validate_plan_options(legs, ledger)
        # reduce_only is self-attested, so it must never OPEN or enlarge a
        # position; the whole plan is reconciled against the ledger PER
        # INSTRUMENT, not per leg. Per-leg checks would let a multi-leg plan
        # carry two BUY-reduce legs on one instrument that each individually
        # "fit" the held long while NETTING to a flip. Where the ledger has a
        # view of the instrument, reduce_only legs that trade WITH the held
        # position (adding to a long via BUY / to a short via SELL) or exceed
        # the position being closed are rejected rather than trusted. A zero
        # ledger quantity means the caller's ledger is not authoritative for
        # that instrument yet (engine-warmup flows rely on reduce_only before
        # fills are reflected), so those legs are left for the broker to
        # adjudicate.
        net_reduce: dict[str, float] = {}
        for leg in legs:
            if not leg.reduce_only:
                continue
            signed = leg.quantity if leg.side is OrderSide.BUY else -leg.quantity
            net_reduce[leg.instrument.id] = net_reduce.get(leg.instrument.id, 0.0) + signed
        for instrument_id, net in net_reduce.items():
            held = ledger.quantity(instrument_id)
            if held == 0:
                continue
            if net > 0 and held > 0:
                raise RiskRejected("reduce_only buy cannot add to a long position")
            if net < 0 and held < 0:
                raise RiskRejected("reduce_only sell cannot add to a short position")
            if abs(held) < abs(net):
                raise RiskRejected("reduce_only order exceeds the position being closed")
        opening = sum(not leg.reduce_only for leg in legs)
        if self.daily_entries is not None:
            # ``reserve`` returns the POST-increment count.
            _day, used = self.daily_entries.reserve(opening)
        else:
            day = datetime.now(timezone.utc).date().isoformat()
            # Keep the same post-increment semantics as the shared counter, or
            # the cap silently allows max_daily_new_positions + 1 openings.
            used = self._local_entries.get(day, 0) + opening
            self._local_entries[day] = used
        if used > self.limits.max_daily_new_positions:
            # Roll back the reservation we just made.
            if self.daily_entries is not None:
                self.daily_entries.release(opening)
            else:
                day = datetime.now(timezone.utc).date().isoformat()
                self._local_entries[day] = max(
                    0, self._local_entries.get(day, 0) - opening
                )
            raise RiskRejected("daily new-position limit exceeded")
        self._authorized[intent.intent_id] = None
        while len(self._authorized) > self.MAX_AUTHORIZED_IDS:
            # Drop the oldest ids one at a time once the set is huge; a dropped
            # id can never be replayed as a duplicate (ids are minted fresh per
            # attempt), so this only bounds memory usage on long-running
            # engines. dict preserves insertion order, so next(iter(...)) is
            # the oldest entry.
            oldest = next(iter(self._authorized))
            del self._authorized[oldest]
        return RiskDecision(True, intent.intent_id)

    def release(self, intent: OrderIntent | MultiLegOrderIntent) -> None:
        """Release a reservation when the adapter definitively rejects submission."""
        if intent.intent_id not in self._authorized:
            return
        del self._authorized[intent.intent_id]
        legs = intent.legs if isinstance(intent, MultiLegOrderIntent) else (intent,)
        opening = sum(not leg.reduce_only for leg in legs)
        if self.daily_entries is not None:
            self.daily_entries.release(opening)
        else:
            day = datetime.now(timezone.utc).date().isoformat()
            self._local_entries[day] = max(
                0, self._local_entries.get(day, 0) - opening
            )

    def _validate_order(self, order) -> None:
        # NaNs pass every <=/> comparison, so each numeric guard pairs the
        # range check with an isfinite check — a NaN quantity/price/quote
        # must fail closed instead of sailing through every gate.
        if not math.isfinite(order.quantity) or order.quantity <= 0:
            raise RiskRejected("quantity must be a positive finite number")
        if (
            not math.isfinite(order.leverage)
            or order.leverage <= 0
            or order.leverage > self.limits.max_leverage
        ):
            raise RiskRejected("maximum leverage exceeded")
        if self.limits.require_fresh_quote:
            if order.quote_timestamp is None:
                raise RiskRejected("fresh quote is required")
            quote_ts = order.quote_timestamp
            if quote_ts.tzinfo is None:
                # Defensive: naive timestamps are interpreted as UTC rather
                # than crashing in subtraction (domain intents reject them
                # at construction; this guards other producers).
                quote_ts = quote_ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - quote_ts).total_seconds()
            if age < 0 or age > self.limits.max_quote_age_seconds:
                raise RiskRejected("quote is stale")
        price = order.limit_price
        if price is None and order.quote_ask is not None:
            price = order.quote_ask
        if price is not None:
            notional = price * order.quantity * order.instrument.multiplier
            if not math.isfinite(notional) or notional > self.limits.max_order_notional:
                raise RiskRejected("maximum order notional exceeded")
        if order.quote_bid is not None and order.quote_ask is not None:
            mid = (order.quote_bid + order.quote_ask) / 2
            if (
                not math.isfinite(mid)
                or not math.isfinite(order.quote_bid)
                or not math.isfinite(order.quote_ask)
                or mid <= 0
                or order.quote_ask < order.quote_bid
            ):
                raise RiskRejected("invalid quote")
            if (order.quote_ask - order.quote_bid) / mid > self.limits.max_spread_pct:
                raise RiskRejected("spread limit exceeded")
        inst = order.instrument
        if inst.id.asset_class in {
            AssetClass.CRYPTO_PERPETUAL,
            AssetClass.CRYPTO_FUTURE,
        }:
            max_lev = getattr(inst, "max_leverage", 1)
            if not math.isfinite(max_lev) or max_lev <= 0:
                raise RiskRejected("invalid leverage limit")
            if order.leverage > max_lev:
                raise RiskRejected("instrument leverage limit exceeded")

    def _validate_plan_options(
        self, legs: tuple[OrderIntent, ...], ledger: PortfolioLedger
    ) -> None:
        """Aggregate nakedness across all legs and existing holdings."""
        call_long: dict[tuple[InstrumentId, date], float] = {}
        call_short: dict[tuple[InstrumentId, date], float] = {}
        put_long: dict[tuple[InstrumentId, date], float] = {}
        put_short: dict[tuple[InstrumentId, date], tuple[float, float, float]] = {}

        def option_key(inst: EquityOption) -> tuple[InstrumentId, date] | None:
            if inst.underlying is None or inst.expiration is None:
                return None
            return inst.underlying, inst.expiration

        for leg in legs:
            inst = leg.instrument
            if not isinstance(inst, EquityOption):
                continue
            key = option_key(inst)
            if key is None or leg.reduce_only:
                continue
            if leg.side is OrderSide.SELL:
                if inst.right is OptionRight.CALL:
                    call_short[key] = call_short.get(key, 0.0) + leg.quantity
                else:
                    qty, strike, mult = put_short.get(
                        key, (0.0, 0.0, inst.multiplier)
                    )
                    put_short[key] = (
                        qty + leg.quantity,
                        max(strike, inst.strike),
                        mult,
                    )
            elif leg.side is OrderSide.BUY:
                if inst.right is OptionRight.CALL:
                    call_long[key] = call_long.get(key, 0.0) + leg.quantity
                else:
                    put_long[key] = put_long.get(key, 0.0) + leg.quantity

        for position in ledger.positions.values():
            inst = position.instrument
            if not isinstance(inst, EquityOption) or position.quantity <= 0:
                continue
            key = option_key(inst)
            if key is None:
                continue
            if inst.right is OptionRight.CALL:
                call_long[key] = call_long.get(key, 0.0) + position.quantity
            else:
                put_long[key] = put_long.get(key, 0.0) + position.quantity

        # Existing SHORT options in the ledger also contribute to nakedness —
        # skipping them (as before) understated the short-call/short-put
        # surface whenever the book already held open obligations. BUY-reduce
        # closing legs are netted out so a legitimate close-and-reopen plan is
        # not double-counted: the close removes the old short, then the new
        # short is measured against the remaining coverage. The closing key
        # MUST include the strike: an expiry-only key would let an orphan
        # BUY-reduce leg (e.g. @90) mask a naked short at a DIFFERENT strike
        # (@100) of the same expiry that it does not actually close.
        def closing_key(inst: EquityOption) -> tuple[InstrumentId, date, float] | None:
            if inst.underlying is None or inst.expiration is None:
                return None
            return inst.underlying, inst.expiration, inst.strike

        closing: dict[tuple[InstrumentId, date, float], float] = {}
        for leg in legs:
            inst = leg.instrument
            if (
                not isinstance(inst, EquityOption)
                or not leg.reduce_only
                or leg.side is not OrderSide.BUY
            ):
                continue
            key = closing_key(inst)
            if key is not None:
                closing[key] = closing.get(key, 0.0) + leg.quantity
        for position in ledger.positions.values():
            inst = position.instrument
            if not isinstance(inst, EquityOption) or position.quantity >= 0:
                continue
            close_key = closing_key(inst)
            if close_key is None:
                continue
            short_qty = max(0.0, -position.quantity - closing.get(close_key, 0.0))
            if short_qty <= 0:
                continue
            key = option_key(inst)
            assert key is not None  # close_key non-None implies underlying set
            if inst.right is OptionRight.CALL:
                call_short[key] = call_short.get(key, 0.0) + short_qty
            else:
                qty, strike, mult = put_short.get(
                    key, (0.0, 0.0, inst.multiplier)
                )
                put_short[key] = (
                    qty + short_qty,
                    max(strike, inst.strike),
                    mult,
                )

        for key, short_qty in call_short.items():
            underlying, _expiry = key
            covered = call_long.get(key, 0.0)
            shares = ledger.quantity(underlying)
            if shares + covered * 100 < short_qty * 100:
                raise RiskRejected("naked call is prohibited")

        required_cash: dict[str, float] = {}
        for key, (qty, strike, mult) in put_short.items():
            uncovered = max(0.0, qty - put_long.get(key, 0.0))
            if uncovered <= 0:
                continue
            # Fail closed on invalid strike/multiplier. NOTE: max(0.0, NaN)
            # collapses to 0.0 in Python (NaN compares False), so a NaN-strike
            # put arrives here as 0.0 — either way the cash requirement must
            # not silently become 0 (0 > cash is also always False).
            if (
                not math.isfinite(strike)
                or strike <= 0
                or not math.isfinite(mult)
                or mult <= 0
            ):
                raise RiskRejected(
                    "uncovered short put has non-finite or non-positive "
                    "strike/multiplier"
                )
            # Use the multiplier recorded from the plan legs' instrument.
            required_cash["USD"] = required_cash.get("USD", 0.0) + (
                uncovered * strike * mult
            )
        for currency, amount in required_cash.items():
            if amount > ledger.cash.get(currency, 0.0):
                raise RiskRejected("uncovered short put is prohibited")

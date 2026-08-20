from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from quantforge.options.manager import OptionCandidate


def _contracts(side_map: dict) -> Iterable[dict]:
    for expirations in side_map.values():
        for contracts in expirations.values():
            yield from contracts


def candidates_from_schwab_chain(
    chain: dict,
    *,
    right: str = "CALL",
) -> tuple[OptionCandidate, ...]:
    side = chain.get("callExpDateMap" if right.upper() == "CALL" else "putExpDateMap")
    results = []
    for row in _contracts(side or {}):
        expiration_value = str(row.get("expirationDate") or "")[:10]
        if not expiration_value:
            continue
        results.append(
            OptionCandidate(
                symbol=str(row["symbol"]),
                strike=float(row["strikePrice"]),
                expiration=date.fromisoformat(expiration_value),
                bid=float(row.get("bid") or 0),
                ask=float(row.get("ask") or 0),
                delta=abs(float(row.get("delta") or 0)),
                open_interest=int(row.get("openInterest") or 0),
                volume=int(row.get("totalVolume") or 0),
            )
        )
    return tuple(results)

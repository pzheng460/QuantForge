"""Canonical action names for the covered-call manager.

Code must reference these constants instead of scattering display-label
strings through logic. The VALUES stay Chinese because they are the
product-facing action labels surfaced by the dashboard API and UI.
"""

#: Open a new covered call against the core position.
OPEN_COVERED_CALL = "开 Covered Call"
#: Buy back the short call to remove remaining gamma/upside risk.
CLOSE_SHORT_CALL = "平仓短 Call"
#: Buy back the short call and do not immediately reopen.
CLOSE_AND_HOLD = "买回后暂不重开"
#: Buy back the short call AND open a replacement at a shallower OTM / later
#: expiry in ONE atomic multi-leg order — no unhedged window between the two
#: legs (the delta trigger path when a viable replacement exists).
ROLL_COVERED_CALL = "滚动 Covered Call"
#: Take no action this cycle.
NO_ACTION = "不操作"

#: Actions the manager considers directly executable by the execution layer.
EXECUTABLE_ACTIONS = frozenset(
    {OPEN_COVERED_CALL, CLOSE_SHORT_CALL, CLOSE_AND_HOLD, ROLL_COVERED_CALL}
)

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GlobalRiskState:
    halted: bool = False
    reason: str = ""
    updated_at: str = ""


class GlobalRiskControl:
    """Persistent process-independent master halt read on every authorization."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(
            path or Path.home() / ".quantforge" / "risk" / "global.json"
        )
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        """Create a default (not-halted) state file on first use.

        This makes a MISSING file unambiguous: after initialization, absence
        can only mean the file was deleted — which must fail closed (halt
        trading) rather than silently re-enabling a halted book.
        """
        try:
            if not self.path.exists():
                self.update(halted=False, reason="initialized")
        except OSError:
            logger.warning(
                "Unable to initialize global risk-control file at %s; "
                "a later missing file will halt trading",
                self.path,
            )

    def get(self) -> GlobalRiskState:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            # The file was deleted AFTER initialization (see
            # _ensure_initialized): deleting the master kill switch must never
            # silently re-enable trading, so a missing file is treated as an
            # operator halt until an operator restores or re-creates it.
            logger.error(
                "Global risk-control file missing at %s — halting trading",
                self.path,
            )
            return GlobalRiskState(
                halted=True,
                reason="risk control file missing",
                updated_at=datetime.now(UTC).isoformat(),
            )
        except (OSError, json.JSONDecodeError):
            # Fail-closed: a master-halt file that is unreadable or corrupt must
            # never silently re-enable trading. Treat it as an operator halt
            # until the file is repaired.
            logger.error(
                "Global risk-control file unreadable at %s — halting trading",
                self.path,
            )
            return GlobalRiskState(
                halted=True,
                reason="risk control file unreadable",
                updated_at=datetime.now(UTC).isoformat(),
            )
        return GlobalRiskState(
            halted=bool(payload.get("halted", False)),
            reason=str(payload.get("reason", "")),
            updated_at=str(payload.get("updated_at", "")),
        )

    def update(self, *, halted: bool, reason: str = "") -> GlobalRiskState:
        state = GlobalRiskState(
            halted=halted,
            reason=reason.strip(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(asdict(state), handle, indent=2)
        os.replace(tmp, self.path)
        self.path.chmod(0o600)
        return state

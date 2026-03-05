"""Generate JSON-Schema-like field descriptors from Python dataclasses."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, List

from web.backend.models import SchemaField


def _snake_to_label(name: str) -> str:
    """Convert snake_case field name to Title Case label."""
    return re.sub(r"_", " ", name).title()


def _type_str(annotation: Any) -> str:
    """Map Python type annotation to a simple UI type string."""
    if annotation in (float,):
        return "float"
    if annotation in (int,):
        return "int"
    if annotation in (bool,):
        return "bool"
    if annotation in (str,):
        return "str"
    # Handle string annotations (forward refs)
    s = str(annotation)
    if "float" in s:
        return "float"
    if "int" in s:
        return "int"
    if "bool" in s:
        return "bool"
    return "str"


def dataclass_to_fields(cls) -> List[SchemaField]:
    """Introspect a dataclass and return a list of SchemaField descriptors."""
    if not dataclasses.is_dataclass(cls):
        return []

    fields = []
    for f in dataclasses.fields(cls):
        # Skip private / internal fields
        if f.name.startswith("_"):
            continue

        default = None
        if f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            try:
                default = f.default_factory()  # type: ignore[misc]
            except Exception:
                default = None

        # Skip non-scalar defaults (lists, dicts) — not suitable for a simple form
        if isinstance(default, (list, dict, tuple)):
            continue

        type_str = _type_str(f.type)

        # Heuristic step / range hints based on name patterns
        step: float | None = None
        min_val: float | None = None
        max_val: float | None = None

        if type_str == "float":
            if "pct" in f.name or "ratio" in f.name or "threshold" in f.name:
                step = 0.01
                min_val = 0.0
                max_val = 1.0
            elif "window" in f.name or "period" in f.name:
                step = 1.0
                min_val = 2.0
                max_val = 500.0
            elif "zscore" in f.name or "entry" in f.name or "stop" in f.name:
                step = 0.1
                min_val = 0.1
                max_val = 10.0
            else:
                step = 0.1
        elif type_str == "int":
            step = 1.0
            min_val = 1.0

        fields.append(
            SchemaField(
                name=f.name,
                type=type_str,
                default=default,
                label=_snake_to_label(f.name),
                min=min_val,
                max=max_val,
                step=step,
            )
        )

    return fields

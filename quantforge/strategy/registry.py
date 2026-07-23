from __future__ import annotations

from quantforge.strategy.api import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register_strategy(cls: type[Strategy]) -> type[Strategy]:
    name = cls.name or cls.__name__.lower()
    if name in _REGISTRY:
        raise ValueError(f"strategy already registered: {name}")
    _REGISTRY[name] = cls
    return cls


def get_strategy(name: str) -> type[Strategy]:
    return _REGISTRY[name]


def list_strategies() -> list[dict]:
    results = []
    for name, cls in sorted(_REGISTRY.items()):
        schema = cls.schema()
        fields = []
        for field_name, spec in schema.get("properties", {}).items():
            fields.append(
                {
                    "name": field_name,
                    "type": (
                        "int"
                        if spec.get("type") == "integer"
                        else spec.get("type", "float")
                    ),
                    "default": spec.get("default"),
                    "label": spec.get("title", field_name),
                    "min": spec.get("minimum"),
                    "max": spec.get("maximum"),
                    "step": None,
                }
            )
        results.append({
            "name": name,
            "display_name": cls.__name__,
            "version": cls.version,
            "engine": "python",
            "config_schema": schema,
            "config_fields": fields,
        })
    return results

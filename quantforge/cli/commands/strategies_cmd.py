"""`quantforge-cli strategies ...` — Pine strategy listing and metadata.

Mirrors web/backend/routers/strategies.py but works from the filesystem,
no server required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

PINE_DIR = Path(__file__).resolve().parents[3] / "quantforge" / "pine" / "strategies"


def _parse_pine(pine_path: Path) -> dict:
    """Parse a .pine file and extract metadata + input parameters."""
    name = pine_path.stem
    title = name.replace("_", " ").title()
    text = pine_path.read_text()
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("strategy("):
            try:
                a = s.index('"') + 1
                b = s.index('"', a)
                title = s[a:b]
            except ValueError:
                pass
            break
    fields: list[dict] = []
    try:
        from quantforge.pine.optimize import extract_pine_inputs
        from quantforge.pine.parser.parser import parse

        ast = parse(text)
        for inp in extract_pine_inputs(ast):
            fields.append(
                {
                    "name": inp.var_name,
                    "type": inp.input_type,
                    "default": (
                        int(inp.defval) if inp.input_type == "int" else inp.defval
                    ),
                    "label": inp.title,
                    "min": inp.minval,
                    "max": inp.maxval,
                    "step": inp.step,
                }
            )
    except Exception:
        pass
    return {
        "name": name,
        "display_name": title,
        "config_fields": fields,
        "path": str(pine_path),
    }


def _list_all() -> list[dict]:
    if not PINE_DIR.exists():
        return []
    return [_parse_pine(p) for p in sorted(PINE_DIR.glob("*.pine"))]


@click.group("strategies")
def strategies_group():
    """List and inspect Pine strategies."""


@strategies_group.command("list")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of table.")
def list_cmd(as_json: bool):
    """List all Pine strategies with their input parameters."""
    items = _list_all()
    if as_json:
        click.echo(json.dumps(items, indent=2))
        return
    if not items:
        click.echo("(no .pine files in quantforge/pine/strategies/)")
        return
    click.echo(f"{'name':<22}  {'display name':<28}  params")
    click.echo("-" * 72)
    for s in items:
        ps = ", ".join(f["name"] for f in s["config_fields"]) or "(none)"
        click.echo(f"{s['name']:<22}  {s['display_name']:<28}  {ps}")


@strategies_group.command("show")
@click.argument("name")
@click.option("--json", "as_json", is_flag=True)
def show_cmd(name: str, as_json: bool):
    """Show metadata for one strategy (by file stem)."""
    for s in _list_all():
        if s["name"] == name:
            if as_json:
                click.echo(json.dumps(s, indent=2))
                return
            click.echo(f"name:         {s['name']}")
            click.echo(f"display:      {s['display_name']}")
            click.echo(f"path:         {s['path']}")
            click.echo("inputs:")
            for f in s["config_fields"]:
                rng = ""
                if f.get("min") is not None or f.get("max") is not None:
                    rng = f"  [{f.get('min')}..{f.get('max')} step={f.get('step')}]"
                click.echo(f"  - {f['name']:<18} {f['type']:<6} default={f['default']}{rng}")
            return
    click.echo(f"strategy '{name}' not found", err=True)
    sys.exit(2)


@strategies_group.command("source")
@click.argument("name")
def source_cmd(name: str):
    """Print the .pine source for a strategy."""
    p = PINE_DIR / f"{name}.pine"
    if not p.exists():
        click.echo(f"strategy '{name}' not found", err=True)
        sys.exit(2)
    click.echo(p.read_text())


@strategies_group.command("rename")
@click.argument("old_name")
@click.argument("new_name")
def rename_cmd(old_name: str, new_name: str):
    """Rename a strategy file (sanitises non-alphanumeric to _)."""
    import re

    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", new_name.removesuffix(".pine"))
    src = PINE_DIR / f"{old_name}.pine"
    dst = PINE_DIR / f"{safe}.pine"
    if not src.exists():
        click.echo(f"strategy '{old_name}' not found", err=True)
        sys.exit(2)
    if dst.exists():
        click.echo(f"strategy '{safe}' already exists", err=True)
        sys.exit(2)
    src.rename(dst)
    click.echo(f"renamed: {old_name} → {safe}")

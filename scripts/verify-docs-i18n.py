"""Verify/record bilingual doc pairs (`README.i18n.yaml`), mirroring the
deepseek-harness convention: README.md (English canonical) + README.zh.md
(Chinese), each carrying a git blob hash recorded at the last confirmed
consistent state. Run when either side changes:

    python scripts/verify-docs-i18n.py --write README.md

Without --write: exit 1 (and reprint hashes) when a file is out of sync.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAIRS: list[tuple[str, str]] = [
    ("README.md", "README.zh.md"),
]


def git_hash(rel: str) -> str:
    out = subprocess.run(
        ["git", "hash-object", rel], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


def load_records() -> dict[str, str]:
    yaml_path = ROOT / "README.i18n.yaml"
    rec: dict[str, str] = {}
    if not yaml_path.exists():
        return rec
    for line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        rec[key.strip()] = value.strip()
    return rec


def write_records(records: dict[str, str]) -> None:
    yaml_path = ROOT / "README.i18n.yaml"
    header = [
        "# Bilingual-pair consistency record: git blob hash of each side at the",
        "# last confirmed-consistent state. Both languages carry equal authority;",
        "# after editing either side, bring the other along and re-record with:",
        "#   python scripts/verify-docs-i18n.py --write README.md",
    ]
    lines = header + [f"{k}: {v}" for k, v in records.items()]
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="update recorded hashes")
    ap.add_argument("files", nargs="*", help="accepted for DSH-style invocation (ignored)")
    args = ap.parse_args(argv)

    mismatched: list[str] = []
    records = load_records()
    for en, zh in PAIRS:
        for rel in (en, zh):
            h = git_hash(rel)
            if records.get(rel) != h:
                mismatched.append(f"{rel}: recorded={records.get(rel, '-')} actual={h}")
    if args.write:
        for en, zh in PAIRS:
            records[en] = git_hash(en)
            records[zh] = git_hash(zh)
        write_records(records)
        print(f"recorded {len(records)} hashes -> README.i18n.yaml")
        return 0
    if mismatched:
        print("MISMATCH (edit synced both sides, then --write):")
        for line in mismatched:
            print("  " + line)
        return 1
    print("README bilingual pair consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

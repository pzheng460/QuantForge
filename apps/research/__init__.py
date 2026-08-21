"""QuantForge research app: market/event data warehouse + event study.

Layers
------
raw      : data/earnings/*.csv, data/prices/*.csv      (idempotent downloads)
warehouse: data/market.duckdb -> prices/earnings/events/catalog (events built in SQL)
study    : reports/surge_event_study_<date>.md          (pure SQL + markdown render)
backup   : ~/.quantforge/data-snapshots/*.tar.gz         (versioned snapshots)

Knowledge
---------
KNOWLEDGE.md in this package is the living knowledge base: validated findings,
data pitfalls, methodology and open TODOs. New reusable lessons go there.
Reports in reports/ are one-off outputs; this file is what persists.

CLI
---
python -m apps.research download earnings|prices [opts]
python -m apps.research import | events | study | screen
python -m apps.research status | verify | manifest | query "SQL" | snapshot | restore
(or: python scripts/research.py <same>)
"""

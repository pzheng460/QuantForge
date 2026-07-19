"""QuantForge-owned news/event collector.

The first implementation normalizes local JSON/JSONL inputs into the event
schema consumed by auto-tune. Network collectors can be added behind the same
function without changing the decision gate.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


def collect_events(sources: list[str | Path], out: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source in sources:
        src = Path(source)
        for raw in _load_source(src):
            events.append(_normalize(raw, source=str(src)))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events)
        + ("\n" if events else "")
    )
    return events


def collect_rss_events(
    urls: list[str],
    out: str | Path,
    *,
    symbols: list[str] | None = None,
    fetcher: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    fetch = fetcher or _fetch_url
    for url in urls:
        for raw in _parse_feed(fetch(url), source=url):
            events.append(
                _normalize(
                    raw | {"symbols": symbols or raw.get("symbols", [])}, source=url
                )
            )
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events)
        + ("\n" if events else "")
    )
    return events


def collect_exchange_status_events(
    urls: list[str],
    out: str | Path,
    *,
    exchange: str,
    symbols: list[str] | None = None,
    fetcher: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    fetch = fetcher or _fetch_url
    for url in urls:
        payload = json.loads(fetch(url))
        for raw in _status_payload_events(payload, exchange=exchange):
            events.append(
                _normalize(
                    raw | {"symbols": symbols or raw.get("symbols", [])}, source=url
                )
            )
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events)
        + ("\n" if events else "")
    )
    return events


def collect_microstructure_events(
    sources: list[str | Path],
    out: str | Path,
    *,
    source_name: str = "market",
    fetcher: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source in sources:
        for raw in _load_micro_source(source, fetcher=fetcher):
            for event in _micro_payload_events(raw, source_name=source_name):
                events.append(_normalize(event, source=source_name))
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(e, sort_keys=True) for e in events)
        + ("\n" if events else "")
    )
    return events


def _load_source(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    if path.suffix == ".json":
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _load_micro_source(
    source: str | Path, *, fetcher: Callable[[str], str] | None = None
) -> list[dict[str, Any]]:
    src = str(source)
    if src.startswith(("http://", "https://")):
        text = (fetcher or _fetch_url)(src)
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    return _load_source(Path(source))


def _normalize(event: dict[str, Any], *, source: str) -> dict[str, Any]:
    normalized = {
        "title": str(event.get("title", "")),
        "summary": str(event.get("summary", event.get("body", ""))),
        "symbols": list(event.get("symbols", [])),
        "source": str(event.get("source") or source),
        "published_at": str(event.get("published_at") or datetime.now(UTC).isoformat()),
    }
    if event.get("url"):
        normalized["url"] = str(event["url"])
    return normalized


def _fetch_url(url: str) -> str:
    req = Request(url, headers={"User-Agent": "QuantForge/1.0"})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode(
            resp.headers.get_content_charset() or "utf-8", errors="replace"
        )


def _status_payload_events(
    payload: dict[str, Any], *, exchange: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    incidents = payload.get("incidents") or payload.get("status") or []
    if isinstance(incidents, dict):
        incidents = [incidents]
    for incident in incidents:
        rows.append(_normalize_status_incident(incident, exchange=exchange))
    announcements = (
        payload.get("announcements")
        or payload.get("notices")
        or payload.get("data")
        or []
    )
    if isinstance(announcements, dict):
        announcements = [announcements]
    for announcement in announcements:
        rows.append(_normalize_announcement(announcement, exchange=exchange))
    return rows


def _normalize_status_incident(
    incident: dict[str, Any], *, exchange: str
) -> dict[str, Any]:
    name = str(
        incident.get("name") or incident.get("title") or incident.get("message") or ""
    )
    status = str(incident.get("status") or incident.get("state") or "status")
    impact = str(incident.get("impact") or incident.get("severity") or "").strip()
    updates = incident.get("incident_updates") or incident.get("updates") or []
    summary = ""
    if updates and isinstance(updates, list):
        summary = str(updates[0].get("body") or updates[0].get("message") or "")
    summary = summary or str(
        incident.get("summary")
        or incident.get("body")
        or incident.get("description")
        or ""
    )
    return {
        "title": " ".join(
            x for x in [exchange, status, impact + ":" if impact else "", name] if x
        ).strip(),
        "summary": summary,
        "url": incident.get("shortlink")
        or incident.get("url")
        or incident.get("link")
        or "",
        "source": exchange,
        "published_at": _normalize_time(
            str(
                incident.get("updated_at")
                or incident.get("created_at")
                or incident.get("published_at")
                or ""
            )
        ),
    }


def _normalize_announcement(
    announcement: dict[str, Any], *, exchange: str
) -> dict[str, Any]:
    title = str(
        announcement.get("title")
        or announcement.get("name")
        or announcement.get("headline")
        or ""
    )
    return {
        "title": f"{exchange} announcement: {title}",
        "summary": str(
            announcement.get("summary")
            or announcement.get("body")
            or announcement.get("description")
            or ""
        ),
        "url": announcement.get("url") or announcement.get("link") or "",
        "source": exchange,
        "published_at": _normalize_time(
            str(
                announcement.get("published_at")
                or announcement.get("updated_at")
                or announcement.get("created_at")
                or ""
            )
        ),
    }


def _micro_payload_events(
    payload: dict[str, Any], *, source_name: str
) -> list[dict[str, Any]]:
    if payload.get("type") in {"funding", "funding_rate"}:
        return [_micro_funding_event(payload, source_name=source_name)]
    if payload.get("type") in {"open_interest", "oi"}:
        return [_micro_open_interest_event(payload, source_name=source_name)]
    if payload.get("type") in {"liquidation", "liquidations"}:
        return [_micro_liquidation_event(payload, source_name=source_name)]
    rows: list[dict[str, Any]] = []
    for item in _as_list(payload.get("funding") or payload.get("funding_rates")):
        rows.append(_micro_funding_event(item, source_name=source_name))
    for item in _as_list(payload.get("open_interest") or payload.get("oi")):
        rows.append(_micro_open_interest_event(item, source_name=source_name))
    for item in _as_list(payload.get("liquidations") or payload.get("liquidation")):
        rows.append(_micro_liquidation_event(item, source_name=source_name))
    return rows


def _micro_funding_event(row: dict[str, Any], *, source_name: str) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("instId") or row.get("instrument") or "")
    rate = float(row.get("funding_rate", row.get("fundingRate", row.get("rate", 0.0))))
    level = "high" if abs(rate) >= 0.001 else "normal"
    return {
        "title": f"{source_name} funding {level}: {symbol} {rate:.4%}",
        "summary": f"funding_rate={rate} may indicate crowded leverage or carry pressure.",
        "symbols": [symbol] if symbol else [],
        "source": source_name,
        "published_at": _normalize_time(
            str(row.get("ts") or row.get("timestamp") or row.get("time") or "")
        ),
    }


def _micro_open_interest_event(
    row: dict[str, Any], *, source_name: str
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("instId") or row.get("instrument") or "")
    change = float(row.get("change_pct", row.get("changePct", row.get("change", 0.0))))
    value = row.get("open_interest", row.get("openInterest", row.get("oi", "")))
    direction = "expansion" if change >= 0 else "contraction"
    return {
        "title": f"{source_name} open interest {direction}: {symbol} {change:+.2%}",
        "summary": f"open_interest={value} change_pct={change:+.4f}.",
        "symbols": [symbol] if symbol else [],
        "source": source_name,
        "published_at": _normalize_time(
            str(row.get("ts") or row.get("timestamp") or row.get("time") or "")
        ),
    }


def _micro_liquidation_event(
    row: dict[str, Any], *, source_name: str
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("instId") or row.get("instrument") or "")
    side = str(row.get("side") or row.get("direction") or "")
    notional = float(
        row.get(
            "notional_usd",
            row.get("notionalUsd", row.get("usd", row.get("amount", 0.0))),
        )
    )
    label = "spike" if notional >= 10_000_000 else "event"
    return {
        "title": f"{source_name} liquidation {label}: {symbol} {side} ${notional:.0f}",
        "summary": f"liquidation notional_usd={notional:.0f} side={side}.",
        "symbols": [symbol] if symbol else [],
        "source": source_name,
        "published_at": _normalize_time(
            str(row.get("ts") or row.get("timestamp") or row.get("time") or "")
        ),
    }


def _as_list(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return [value] if isinstance(value, dict) else []


def _parse_feed(xml_text: str, *, source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    tag = _local_name(root.tag)
    if tag == "rss":
        return [
            _parse_rss_item(item, source=source) for item in root.findall(".//item")
        ]
    if tag == "feed":
        return [
            _parse_atom_entry(entry, source=source)
            for entry in _children(root, "entry")
        ]
    return []


def _parse_rss_item(item: ET.Element, *, source: str) -> dict[str, Any]:
    return {
        "title": _child_text(item, "title"),
        "summary": _child_text(item, "description"),
        "url": _child_text(item, "link"),
        "source": source,
        "published_at": _normalize_time(
            _child_text(item, "pubDate") or _child_text(item, "date")
        ),
    }


def _parse_atom_entry(entry: ET.Element, *, source: str) -> dict[str, Any]:
    link = ""
    for child in list(entry):
        if _local_name(child.tag) == "link":
            link = child.attrib.get("href", "")
            break
    return {
        "title": _child_text(entry, "title"),
        "summary": _child_text(entry, "summary") or _child_text(entry, "content"),
        "url": link,
        "source": source,
        "published_at": _normalize_time(
            _child_text(entry, "updated") or _child_text(entry, "published")
        ),
    }


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _child_text(element: ET.Element, name: str) -> str:
    for child in list(element):
        if _local_name(child.tag) == name:
            return "".join(child.itertext()).strip()
    return ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_time(value: str) -> str:
    if not value:
        return datetime.now(UTC).isoformat()
    try:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00").isoformat()
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()

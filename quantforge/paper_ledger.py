"""File-backed paper/shadow execution ledger."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PaperLedger:
    def __init__(
        self,
        path: str | Path | None = None,
        *,
        initial_equity: float = 100_000.0,
    ) -> None:
        self.path = (
            Path(path) if path else Path.home() / ".quantforge" / "paper_ledger.json"
        )
        self.initial_equity = float(initial_equity)
        self.backend = (
            "sqlite"
            if self.path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            else "json"
        )

    def record_signal(
        self,
        *,
        strategy_id: str,
        role: str,
        side: str,
        price: float,
        quantity: float,
        ts: str | None = None,
        version_id: str = "",
        fee_rate: float = 0.0,
        slippage_bps: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {"promoted", "paper", "shadow"}:
            raise ValueError("role must be promoted, paper, or shadow")
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if price <= 0 or quantity <= 0:
            raise ValueError("price and quantity must be positive")

        data = self._read()
        key = _position_key(strategy_id, role)
        position = data.setdefault("positions", {}).get(key) or {
            "strategy_id": strategy_id,
            "role": role,
            "quantity": 0.0,
            "avg_price": 0.0,
            "realized_pnl": 0.0,
        }
        fill_price = _fill_price(side, float(price), float(slippage_bps))
        signed_qty = float(quantity) if side == "buy" else -float(quantity)
        prior_realized = float(position.get("realized_pnl", 0.0))
        updated = _apply_fill(position, signed_qty, fill_price)
        fee = abs(fill_price * float(quantity)) * float(fee_rate)
        updated["realized_pnl"] = round(float(updated["realized_pnl"]) - fee, 10)

        realized_delta = float(updated["realized_pnl"]) - prior_realized
        equity = self._last_equity(data, strategy_id, role) + realized_delta
        high_water = max(
            self.initial_equity, self._high_water(data, strategy_id, role), equity
        )
        drawdown = (
            0.0 if high_water <= 0 else max(0.0, (high_water - equity) / high_water)
        )

        event_ts = ts or datetime.now(UTC).isoformat()
        signal = {
            "ts": event_ts,
            "strategy_id": strategy_id,
            "role": role,
            "version_id": version_id,
            "side": side,
            "price": float(price),
            "quantity": float(quantity),
            "metadata": metadata or {},
        }
        fill = {
            "ts": event_ts,
            "strategy_id": strategy_id,
            "role": role,
            "version_id": version_id,
            "side": side,
            "price": round(fill_price, 10),
            "quantity": float(quantity),
            "fee": round(fee, 10),
            "realized_pnl_delta": round(realized_delta, 10),
        }
        equity_point = {
            "ts": event_ts,
            "strategy_id": strategy_id,
            "role": role,
            "equity": round(equity, 10),
            "realized_pnl": round(float(updated["realized_pnl"]), 10),
            "drawdown": round(drawdown, 10),
        }
        data.setdefault("signals", []).append(signal)
        data.setdefault("fills", []).append(fill)
        data.setdefault("equity", []).append(equity_point)
        data["positions"][key] = updated
        self._write(data)
        return {
            "signal": signal,
            "fill": fill,
            "position": updated,
            "equity": equity_point,
        }

    def summary(self, strategy_id: str, *, role: str | None = None) -> dict[str, Any]:
        data = self._read()
        roles = [role] if role else ["promoted", "paper", "shadow"]
        summaries = [
            _summary_for(data, strategy_id, r, self.initial_equity) for r in roles
        ]
        return (
            summaries[0] if role else {"strategy_id": strategy_id, "roles": summaries}
        )

    def _last_equity(self, data: dict[str, Any], strategy_id: str, role: str) -> float:
        points = [
            point
            for point in data.get("equity", [])
            if point.get("strategy_id") == strategy_id and point.get("role") == role
        ]
        return float(points[-1]["equity"]) if points else self.initial_equity

    def _high_water(self, data: dict[str, Any], strategy_id: str, role: str) -> float:
        points = [
            float(point.get("equity", self.initial_equity))
            for point in data.get("equity", [])
            if point.get("strategy_id") == strategy_id and point.get("role") == role
        ]
        return max(points) if points else self.initial_equity

    def _read(self) -> dict[str, Any]:
        if self.backend == "sqlite":
            return self._read_sqlite()
        if not self.path.exists():
            return {
                "version": 1,
                "signals": [],
                "fills": [],
                "positions": {},
                "equity": [],
            }
        return json.loads(self.path.read_text())

    def _write(self, data: dict[str, Any]) -> None:
        if self.backend == "sqlite":
            self._write_sqlite(data)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.replace(self.path)

    def _read_sqlite(self) -> dict[str, Any]:
        self._ensure_sqlite()
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            signals = [
                {
                    "ts": row["ts"],
                    "strategy_id": row["strategy_id"],
                    "role": row["role"],
                    "version_id": row["version_id"],
                    "side": row["side"],
                    "price": row["price"],
                    "quantity": row["quantity"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                }
                for row in conn.execute("select * from signals order by id")
            ]
            fills = [
                {
                    "ts": row["ts"],
                    "strategy_id": row["strategy_id"],
                    "role": row["role"],
                    "version_id": row["version_id"],
                    "side": row["side"],
                    "price": row["price"],
                    "quantity": row["quantity"],
                    "fee": row["fee"],
                    "realized_pnl_delta": row["realized_pnl_delta"],
                }
                for row in conn.execute("select * from fills order by id")
            ]
            positions = {
                _position_key(row["strategy_id"], row["role"]): {
                    "strategy_id": row["strategy_id"],
                    "role": row["role"],
                    "quantity": row["quantity"],
                    "avg_price": row["avg_price"],
                    "realized_pnl": row["realized_pnl"],
                }
                for row in conn.execute("select * from positions")
            }
            equity = [
                {
                    "ts": row["ts"],
                    "strategy_id": row["strategy_id"],
                    "role": row["role"],
                    "equity": row["equity"],
                    "realized_pnl": row["realized_pnl"],
                    "drawdown": row["drawdown"],
                }
                for row in conn.execute("select * from equity order by id")
            ]
        return {
            "version": 1,
            "signals": signals,
            "fills": fills,
            "positions": positions,
            "equity": equity,
        }

    def _write_sqlite(self, data: dict[str, Any]) -> None:
        self._ensure_sqlite()
        with sqlite3.connect(self.path) as conn:
            conn.execute("delete from signals")
            conn.execute("delete from fills")
            conn.execute("delete from positions")
            conn.execute("delete from equity")
            conn.executemany(
                """
                insert into signals(ts, strategy_id, role, version_id, side, price, quantity, metadata)
                values(:ts, :strategy_id, :role, :version_id, :side, :price, :quantity, :metadata)
                """,
                [
                    signal
                    | {
                        "metadata": json.dumps(
                            signal.get("metadata") or {}, sort_keys=True
                        )
                    }
                    for signal in data.get("signals", [])
                ],
            )
            conn.executemany(
                """
                insert into fills(ts, strategy_id, role, version_id, side, price, quantity, fee, realized_pnl_delta)
                values(:ts, :strategy_id, :role, :version_id, :side, :price, :quantity, :fee, :realized_pnl_delta)
                """,
                data.get("fills", []),
            )
            conn.executemany(
                """
                insert into positions(strategy_id, role, quantity, avg_price, realized_pnl)
                values(:strategy_id, :role, :quantity, :avg_price, :realized_pnl)
                """,
                list(data.get("positions", {}).values()),
            )
            conn.executemany(
                """
                insert into equity(ts, strategy_id, role, equity, realized_pnl, drawdown)
                values(:ts, :strategy_id, :role, :equity, :realized_pnl, :drawdown)
                """,
                data.get("equity", []),
            )

    def _ensure_sqlite(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executescript(
                """
                create table if not exists signals (
                    id integer primary key autoincrement,
                    ts text not null,
                    strategy_id text not null,
                    role text not null,
                    version_id text not null,
                    side text not null,
                    price real not null,
                    quantity real not null,
                    metadata text not null
                );
                create table if not exists fills (
                    id integer primary key autoincrement,
                    ts text not null,
                    strategy_id text not null,
                    role text not null,
                    version_id text not null,
                    side text not null,
                    price real not null,
                    quantity real not null,
                    fee real not null,
                    realized_pnl_delta real not null
                );
                create table if not exists positions (
                    strategy_id text not null,
                    role text not null,
                    quantity real not null,
                    avg_price real not null,
                    realized_pnl real not null,
                    primary key(strategy_id, role)
                );
                create table if not exists equity (
                    id integer primary key autoincrement,
                    ts text not null,
                    strategy_id text not null,
                    role text not null,
                    equity real not null,
                    realized_pnl real not null,
                    drawdown real not null
                );
                """
            )


def _position_key(strategy_id: str, role: str) -> str:
    return f"{strategy_id}:{role}"


def _fill_price(side: str, price: float, slippage_bps: float) -> float:
    slip = slippage_bps / 10_000.0
    return price * (1 + slip if side == "buy" else 1 - slip)


def _apply_fill(
    position: dict[str, Any], signed_qty: float, price: float
) -> dict[str, Any]:
    qty = float(position.get("quantity", 0.0))
    avg = float(position.get("avg_price", 0.0))
    realized = float(position.get("realized_pnl", 0.0))
    if qty == 0 or (qty > 0 and signed_qty > 0) or (qty < 0 and signed_qty < 0):
        new_qty = qty + signed_qty
        new_avg = ((abs(qty) * avg) + (abs(signed_qty) * price)) / abs(new_qty)
        return {
            **position,
            "quantity": round(new_qty, 10),
            "avg_price": round(new_avg, 10),
            "realized_pnl": realized,
        }

    closing_qty = min(abs(qty), abs(signed_qty))
    if qty > 0:
        realized += (price - avg) * closing_qty
    else:
        realized += (avg - price) * closing_qty
    new_qty = qty + signed_qty
    new_avg = 0.0 if new_qty == 0 else price
    return {
        **position,
        "quantity": round(new_qty, 10),
        "avg_price": round(new_avg, 10),
        "realized_pnl": round(realized, 10),
    }


def _summary_for(
    data: dict[str, Any], strategy_id: str, role: str, initial_equity: float
) -> dict[str, Any]:
    position = data.get("positions", {}).get(_position_key(strategy_id, role)) or {}
    fills = [
        fill
        for fill in data.get("fills", [])
        if fill.get("strategy_id") == strategy_id and fill.get("role") == role
    ]
    equity_points = [
        point
        for point in data.get("equity", [])
        if point.get("strategy_id") == strategy_id and point.get("role") == role
    ]
    equity = float(equity_points[-1]["equity"]) if equity_points else initial_equity
    max_drawdown = max([float(p.get("drawdown", 0.0)) for p in equity_points] or [0.0])
    return {
        "strategy_id": strategy_id,
        "role": role,
        "n_fills": len(fills),
        "position_qty": round(float(position.get("quantity", 0.0)), 10),
        "avg_price": round(float(position.get("avg_price", 0.0)), 10),
        "realized_pnl": round(float(position.get("realized_pnl", 0.0)), 10),
        "equity": round(equity, 10),
        "max_drawdown": round(max_drawdown, 10),
    }

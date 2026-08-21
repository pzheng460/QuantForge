"""Options research: live chain snapshots, IV/skew/term-structure analysis.

Multi-asset research layer for US-equity options. Reads live chains through a
read-only Schwab connector (market-data product; never submits orders — order
submission stays in the dashboard/risk layer), snapshots them to
`data/options/chains_*.csv` (+ DuckDB `option_chains`), and computes research
metrics: ATM IV, 25-delta skew, term structure, and covered-call candidates.

Output: reports/options_research_<date>.md
"""
from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd

from . import config

REPORT_PATH = os.path.join(config.ROOT, "reports", f"options_research_{date.today():%Y-%m-%d}.md")


def _connector():
    """Read-only Schwab connector (market-data product)."""
    from quantforge.brokers.schwab import SchwabConnector, credentials_for

    return SchwabConnector(
        credentials_for("trading"),
        market_credentials=credentials_for("market_data"),
    )


def _chain_records(ticker: str, chain: dict, snapshot_at) -> pd.DataFrame:
    """Flatten Schwab callExpDateMap/putExpDateMap into one DataFrame."""
    records: list[dict] = []
    for right, mkey in (("C", "callExpDateMap"), ("P", "putExpDateMap")):
        exp_map = chain.get(mkey) or {}
        for exp_key, strikes in exp_map.items():
            exp_date = exp_key.split(":")[0] if ":" in exp_key else exp_key
            for strike_key, opts in strikes.items():
                for o in opts:
                    try:
                        records.append({
                            "snapshot_at": snapshot_at,
                            "ticker": ticker,
                            "symbol": o.get("symbol"),
                            "expiration": pd.Timestamp(exp_date).date(),
                            "strike": float(strike_key),
                            "right": right,
                            "bid": o.get("bid"),
                            "ask": o.get("ask"),
                            "last": o.get("last"),
                            "iv": o.get("volatility"),  # Schwab field for IV (percent)
                            "theo_iv": o.get("theoreticalVolatility"),
                            "dte": o.get("daysToExpiration"),
                            "delta": o.get("delta"),
                            "gamma": o.get("gamma"),
                            "theta": o.get("theta"),
                            "vega": o.get("vega"),
                            "open_interest": o.get("openInterest"),
                            "volume": o.get("totalVolume"),
                        })
                    except (TypeError, ValueError):
                        continue
    return pd.DataFrame(records)


def _metrics(df: pd.DataFrame, spot: float) -> dict:
    """Compact research metrics for one chain snapshot."""
    m: dict[str, object] = {"spot": spot}
    if df.empty:
        m["status"] = "无期权数据"
        return m
    atm_call = df[(df.right == "C") & (df.strike.notna())].copy()
    atm_call["dist"] = (atm_call["strike"] - spot).abs()
    if atm_call.empty:
        m["status"] = "无CALL数据"
        return m
    # front expiry = earliest with >=5 DTE (avoid 0dte/1dte weekly noise)
    exp = min(
        (e for e in atm_call["expiration"].unique()
         if atm_call.loc[atm_call.expiration == e, "dte"].fillna(99).min() >= 5),
        default=None,
    )
    if exp is None:
        exp = atm_call["expiration"].min()
    d = atm_call[atm_call.expiration == exp]
    m["第1到期"] = str(exp)
    atm = d.loc[d.dist.idxmin()]
    m["ATM IV (call)"] = round(atm["iv"], 1) if atm["iv"] is not None else None
    # NOTE: Schwab's `volatility` field is strike-symmetric (call/put identical
    # at the same strike+expiry), so a put/call IV ratio would be constant 1.0.
    # Skew is therefore measured on the CALL side only.
    # term structure: ATM call IV at 1st vs 2nd distinct expiry
    exps = sorted(df.expiration.unique())
    if len(exps) >= 2:
        d2 = atm_call[atm_call.expiration == exps[1]]
        if not d2.empty:
            atm2 = d2.loc[(d2.strike - spot).abs().idxmin()]
            m["第2到期 IV"] = round(atm2["iv"], 1) if atm2["iv"] is not None else None
            m["近/远"] = "远端更高(预期升波)" if (m.get("第2到期 IV") or 0) >= (m["ATM IV (call)"] or 0) else "近端更高"
    # 25-delta call skew on front expiry
    c25 = d[(d.delta.notna()) & (d.delta <= 0.35) & (d.delta >= 0.15)]
    if not c25.empty:
        iv25 = c25.loc[c25["delta"].idxmax()]["iv"]
        m["25Δ call IV"] = round(iv25, 1) if iv25 is not None else None
        m["skew(25Δcall-ATM)"] = round(iv25 - atm["iv"], 1) if (iv25 is not None and atm["iv"] is not None) else None
    m["候选数"] = int(len(df))
    m["status"] = "ok"
    return m


def chain_metrics_from_raw(ticker: str, chain: dict, spot: float) -> dict:
    """Research metrics computed from an already-fetched Schwab chain dict.

    Reusable by the dashboard analysis endpoint (analysis lives here; order
    submission stays in dashboard/risk).
    """
    rec = _chain_records(ticker, chain, datetime.now())
    return {"ticker": ticker, **_metrics(rec, spot), "条数": len(rec)}


def snapshot_chains(tickers: list[str] | None = None, *, save_csv: bool = True) -> tuple[list[dict], dict]:
    """Fetch live chains, snapshot them (csv + duckdb-ready frame), return metrics + raw frame."""
    tickers = tickers or config.OPTIONS_UNIVERSE
    connector = _connector()
    now = datetime.now()
    frames: list[pd.DataFrame] = []
    metrics: list[dict] = []
    os.makedirs(config.OPTN, exist_ok=True)
    for tk in tickers:
        try:
            spot = connector.get_quote_price(tk)
            chain = connector.get_option_chain(tk, contract_type="ALL", strike_count=60)
        except Exception as exc:  # noqa: BLE001
            metrics.append({"ticker": tk, "status": f"err:{type(exc).__name__}"})
            continue
        rec = _chain_records(tk, chain, now)
        met = {"ticker": tk, **_metrics(rec, spot), "条数": len(rec)}
        metrics.append(met)
        if save_csv and not rec.empty:
            path = os.path.join(config.OPTN, f"chains_{tk}_{now:%Y%m%d}.csv")
            rec.to_csv(path, index=False)
            frames.append(rec)
        print(f"  {tk}: {len(rec)} 期权条目, ATM IV {met.get('ATM IV (call)')}", flush=True)
    raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return metrics, raw


def build_report() -> str:
    metrics, raw = snapshot_chains()
    md: list[str] = []
    md.append("# 期权研究（第一版）：实时链快照 + IV/偏斜/期限结构")
    md.append("")
    md.append("- 数据：Schwab 实时链（market-data，只读），快照存 `data/options/` + DuckDB `option_chains`")
    md.append("- 范围：美股大盘 ETF + 高流动性个股（可扩展任意 ticker）；将来可叠加事件研究（财报前 IV 研判）")
    md.append("")
    if raw.empty and not metrics:
        md.append("（无法拉取任何链——检查 Schwab market-data 凭证）")
    else:
        df = pd.DataFrame(metrics)
        show = df[[c for c in ["ticker", "spot", "ATM IV (call)",
                               "25Δ call IV", "skew(25Δcall-ATM)", "第1到期", "第2到期 IV", "近/远", "条数"]
                   if c in df.columns]]
        md.append(show.to_markdown(index=False))
        md.append("")
        md.append("> 读法：ATM IV=隐含波动率水平；skew(25Δcall-ATM)>0=远端call偏贵（追涨情绪）；")
        md.append("> 近/远=期限结构（远端更高=预期升波/正常contango；近端更高=临近事件/恐慌）。")
        md.append("> 数据限制：Schwab `volatility` 为 strike 级（C/P 对称），偏斜仅能由 call 侧度量。")
        md.append("")
        md.append("## 备兑研究候选（第一版）")
        md.append("")
        md.append("对「大盘 ETF + 横盘/温和涨」组合，候选 = 前端到期、ATM IV 中等偏高、无财报近在咫尺；")
        md.append("数量化筛选（IV 与 skew）留给事件研究叠加后做第二版。当前快照仅供观察链质量与流动性。")
        md.append("")
    text = "\n".join(md)
    with open(REPORT_PATH, "w") as fh:
        fh.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:1600])
    return text


def main(argv: list[str] | None = None) -> int:
    build_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

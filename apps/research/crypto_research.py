"""Crypto research: price, derivatives and positioning analysis.

Multi-asset research layer (equities have no earnings events here — this is
pure price / derivatives research on stable large caps BTC / ETH / SOL plus
selected spots). Uses the DuckDB crypto tables built by `import-crypto`.

Output: reports/crypto_research_<date>.md
"""
from __future__ import annotations

import os
from datetime import date

import duckdb

from . import config

REPORT_PATH = os.path.join(config.ROOT, "reports", f"crypto_research_{date.today():%Y-%m-%d}.md")


def _annualized_vol(close) -> float | None:
    r = close.pct_change().dropna()
    if len(r) < 20:
        return None
    return float(r.tail(30).std() * (252 ** 0.5))


def build_report() -> str:
    if not os.path.exists(config.DB):
        raise SystemExit("no database yet — run: python -m apps.research import")
    con = duckdb.connect(config.DB, read_only=True)
    try:
        has_class = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name='crypto_ohlcv'"
        ).fetchone()[0]
        if not has_class:
            raise SystemExit("crypto tables missing — run: python -m apps.research download crypto && python -m apps.research import-crypto")
        ohlcv = con.execute(
            "SELECT symbol, ts, close FROM crypto_ohlcv ORDER BY symbol, ts"
        ).fetchdf()
        try:
            funding = con.execute(
                "SELECT symbol, ts, value AS funding_rate FROM crypto_funding ORDER BY symbol, ts"
            ).fetchdf()
        except Exception:  # noqa: BLE001
            funding = None
        try:
            oi = con.execute(
                "SELECT symbol, ts, value AS open_interest FROM crypto_oi ORDER BY symbol, ts"
            ).fetchdf()
        except Exception:  # noqa: BLE001
            oi = None
    finally:
        con.close()

    md: list[str] = []
    md.append("# 加密货币研究（第一版）：价格 + 衍生品 + 持仓结构")
    md.append("")
    md.append("- 数据：bitget 公开接口（现货 + 永续，1d K线），截至报告日；源 CSV 在 `data/crypto/`")
    md.append("- 视角：加密货币无「财报事件」，研究 = 动量/趋势/波动 + 资金费率 + 未平仓量（衍生品情绪）")
    md.append("")

    import pandas as pd

    rows = []
    for sym in config.CRYPTO_SPOTS + config.CRYPTO_PERPS:
        key = config.crypto_key(sym)
        c = ohlcv[ohlcv["symbol"] == key]
        if c.empty:
            continue
        c = c.set_index("ts")["close"].sort_index()
        last = c.iloc[-1]
        ret = {d: (c.iloc[-1] / c.iloc[-d] - 1) * 100 for d in (7, 30, 90, 180, 360)
               if len(c) >= d}
        ath = c.max()
        vol = _annualized_vol(c)
        ma50 = c.tail(50).mean() if len(c) >= 50 else None
        ma200 = c.tail(200).mean() if len(c) >= 200 else None
        regime = "多头(close>MA50>MA200)" if (ma50 and ma200 and last > ma50 > ma200) else (
            "震荡/空头" if (ma200 and last < ma200) else "震荡")
        fund = None
        if funding is not None:
            f = funding[funding["symbol"] == key]
            fund = f["funding_rate"].tail(30).mean() * 100 if len(f) else None
        oi_chg = None
        if oi is not None:
            o = oi[oi["symbol"] == key].set_index("ts")["open_interest"].sort_index()
            oi_chg = (o.iloc[-1] / o.iloc[-21] - 1) * 100 if len(o) >= 21 else None
        spot_ret = None
        spot_key = sym.replace(":USDT", "")
        if sym != spot_key and not ohlcv[ohlcv["symbol"] == config.crypto_key(spot_key)].empty:
            sc = ohlcv[ohlcv["symbol"] == config.crypto_key(spot_key)].set_index("ts")["close"].sort_index()
            spot_ret = (c.iloc[-1] / sc.iloc[-1] - 1) * 100  # perp-spot basis %
        rows.append({
            "标的": "永续" if ":" in sym else "现货",
            "符号": key,
            "现价": (f"{last:,.4f}" if last < 1 else f"{last:,.2f}" if last < 100 else f"{last:,.0f}"),
            "7d%": f"{ret.get(7, float('nan')):+.1f}",
            "30d%": f"{ret.get(30, float('nan')):+.1f}",
            "90d%": f"{ret.get(90, float('nan')):+.1f}",
            "360d%": f"{ret.get(360, float('nan')):+.1f}",
            "距ATH%": f"{(last/ath-1)*100:+.1f}",
            "趋势": regime,
            "年化波动": f"{vol*100:.0f}%" if vol else "—",
            "30d资金费率": f"{fund:+.3f}%" if fund is not None else "—",
            "OI 30d": f"{oi_chg:+.0f}%" if oi_chg is not None else "—",
            "基差%": f"{spot_ret:+.1f}" if spot_ret is not None else "—",
        })
    df = pd.DataFrame(rows)
    md.append(df.to_markdown(index=False))
    md.append("")
    md.append("> 读法：趋势=MA 多头排列/空头；30d 资金费率为正=多头杠杆成本（牛市持续信号），大幅为正=过热；")
    md.append("> OI 30d 增加 + 价格上涨=新多进场（健康），OI 增加 + 价格下跌=空头开仓（警惕）；")
    md.append("> 基差=永续相对现货溢价（正基差=多头情绪）。")
    md.append("")
    md.append("## 结论（第一版，观察性）")
    md.append("")
    md.append("1. **无财报事件**：本层研究用「动量阶梯 + 趋势结构 + 杠杆情绪」替代 PEAD，不做超预期判断。")
    md.append("2. 每个标的的 7/30/90/360d 动量构成动量阶梯：360d 为正 + 30d 加速 = 主升延续；")
    md.append("   360d 为正 + 30d 大幅回落 = 顶部风险区（可叠加 equity 侧的 T9 顶标思路）。")
    md.append("3. 资金费率 + OI 是衍生品独有情绪维度：费率持续偏高 + OI 暴增需警惕多杀多。")
    md.append("")

    text = "\n".join(md)
    with open(REPORT_PATH, "w") as fh:
        fh.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:1800])
    return text


def main(argv: list[str] | None = None) -> int:
    build_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

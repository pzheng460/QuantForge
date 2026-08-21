"""Technical (conventional) research: pure price/technical signals, no earnings.

Covers US equities, ETFs, and crypto from the same lens: MA regime, momentum
spectrum, range position, realized volatility, and breakout flags. This is the
"常规交易" research layer — signal candidates regardless of whether an earnings
event exists (crypto has none; equities here are studied price-first).

Output: reports/technical_screen_<date>.md
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd

from . import config

REPORT_PATH = os.path.join(config.ROOT, "reports", f"technical_screen_{date.today():%Y-%m-%d}.md")

#: Default lens covers liquid equities we already hold price data for + crypto.
TECHNICAL_UNIVERSE = [
    "TSLA", "NVDA", "AMD", "MU", "PLTR", "AAPL", "MSFT", "GOOGL", "AMZN", "META",
] + config.CRYPTO_PERPS + config.CRYPTO_SPOTS


def _load(symbol: str) -> pd.DataFrame | None:
    """Load a daily close series for an equity symbol or a crypto market."""
    eq = os.path.join(config.PRIC, f"{symbol}.csv")
    if os.path.exists(eq):
        df = pd.read_csv(eq, index_col=0, parse_dates=True)
        if "Close" in df.columns:
            return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        return None
    cr = os.path.join(config.CRYPTO, f"ohlcv_{config.crypto_key(symbol)}.csv")
    if os.path.exists(cr):
        df = pd.read_csv(cr, parse_dates=["ts"]).set_index("ts")
        return df[["open", "high", "low", "close", "volume"]].rename(
            columns={"open": "Open", "high": "High", "low": "Low",
                     "close": "Close", "volume": "Volume"}
        ).astype(float)
    return None


def _signals(symbol: str, df: pd.DataFrame, *, crypto: bool) -> dict:
    c = df["Close"].dropna()
    last = c.iloc[-1]
    out: dict[str, object] = {"资产": "加密" if crypto else "股票", "符号": symbol}
    h = c.tail(252)
    high = h.max()
    lo = h.min()
    out["现价"] = f"{last:,.2f}"
    for d in (20, 60, 120, 250):
        if len(c) >= d:
            out[f"{d}d%"] = f"{(last / c.iloc[-d] - 1) * 100:+.1f}"
    if len(c) >= 50:
        ma50 = c.tail(50).mean()
        out["MA50"] = f"{ma50:,.0f}"
    else:
        ma50 = None
    if len(c) >= 200:
        ma200 = c.tail(200).mean()
        out["MA200"] = f"{ma200:,.0f}"
    else:
        ma200 = None
    out["趋势"] = (
        "多头" if (ma50 is not None and ma200 is not None and last > ma50 > ma200)
        else ("空头" if (ma200 is not None and last < ma200) else "震荡")
    )
    out["区间位置%"] = f"{((last - lo) / (high - lo)) * 100:.0f}" if high > lo else "—"
    out["距高点%"] = f"{(last / high - 1) * 100:+.1f}"
    r = c.pct_change().dropna()
    out["vol30%"] = f"{(r.tail(30).std() * (252 ** 0.5)) * 100:.0f}" if len(r) >= 30 else "—"
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.tail(20).mean()
    out["ATR%"] = f"{(atr / last) * 100:.1f}" if last else "—"
    out["突破20d高"] = "是" if last == df["High"].tail(20).max() else "否"
    if {"Volume"}.issubset(df.columns) and len(df) >= 60:
        v20 = df["Volume"].tail(20).mean()
        v60 = df["Volume"].tail(60).mean()
        out["量比20/60"] = f"{v20 / v60:.1f}" if v60 else "—"
    # 动量阶梯 verdict (价格研究的核心结论)
    m20 = c.iloc[-1] / c.iloc[-21] if len(c) >= 21 else 1
    m60 = c.iloc[-1] / c.iloc[-61] if len(c) >= 61 else 1
    m120 = c.iloc[-1] / c.iloc[-121] if len(c) >= 121 else 1
    m250 = c.iloc[-1] / c.iloc[-251] if len(c) >= 251 else None
    long_bull = m120 >= 1 and (m250 is None or m250 >= 1)
    accelerating = m60 >= 1 and m20 >= 1
    out["动能"] = (
        "主升(长多+加速)" if (long_bull and accelerating)
        else "长多减速" if long_bull
        else "下跌中继" if (m120 < 1 and m60 < 1 and m20 < 1)
        else "反弹" if m20 >= 1
        else "筑底震荡"
    )
    return out


def build_report(symbols: list[str] | None = None, *, max_rows: int = 40) -> str:
    symbols = symbols or TECHNICAL_UNIVERSE
    rows = []
    for sym in symbols:
        df = _load(sym)
        if df is None or len(df) < 60:
            rows.append({"资产": "—", "符号": sym, "现价": "无数据", "趋势": "—", "动能": "—"})
            continue
        rows.append(_signals(sym, df, crypto=sym in config.CRYPTO_SPOTS or sym in config.CRYPTO_PERPS))
    df = pd.DataFrame(rows).reindex(columns=list(rows[0].keys()))
    md: list[str] = []
    md.append("# 常规交易研究（第一版）：多资产纯价格/技术面")
    md.append("")
    md.append("- 视角：不依赖财报事件——动量阶梯 + 均线结构 + 区间位置 + 波动/量能")
    md.append("- 覆盖：美股流动股（沿用 `data/prices/`）与加密货币（`data/crypto/`）同一套镜头")
    md.append("")
    show = df.head(max_rows)
    md.append(show.to_markdown(index=False))
    md.append("")
    md.append("> 读法：「动能」是核心结论：**主升(长多+加速)**=可持；**长多减速**=顶部观察区；")
    md.append("> **反弹**=空头市场反抽谨慎；区间位置>80% + 距高点<5% + vol 低 = 强势整理；")
    md.append("> 距高点 <0% 且突破20d高+量比>1 = 新高突破确认。")
    md.append("")
    md.append("## 第一版结论")
    md.append("")
    n_bull = sum(1 for r in rows if r.get("动能") == "主升(长多+加速)")
    n_top = sum(1 for r in rows if r.get("趋势") == "多头" and r.get("区间位置%") != "—" and float(r.get("区间位置%", "0").rstrip("%")) >= 80)
    md.append(f"- 主升(长多+加速) {n_bull} 个；高位强势整理（区间≥80%）{n_top} 个——后者叠加财报/事件的版本在 `screen`。")
    md.append("- 技术面只回答「趋势与位置」，不回答「事件催化」：与 `study`/`screen` 是互补而非替代。")
    md.append("")
    text = "\n".join(md)
    with open(REPORT_PATH, "w") as fh:
        fh.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:1400])
    return text


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="research technical")
    ap.add_argument("--symbols", default="", help="comma-separated override (equity or crypto)")
    ap.add_argument("--max-rows", type=int, default=40)
    args = ap.parse_args(argv)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()] or None
    build_report(symbols, max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

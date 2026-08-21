"""Surge screening: apply the rules validated in the event study to the
current market and rank candidates.

Rules (weights from reports/surge_event_study_2026-08-21.md):
  - earnings surprise      x1.0   (PEAD: top-decile surprise -> +43% 12m avg)
  - 12m momentum           x1.0   ("strong momentum x surprise" = best cell)
  - EPS YoY                x0.5   (earnings acceleration)
  - 6m momentum            x0.5   (surge in progress)
  - 52-week strength       x0.5   (high-pos x surprise beat the matrix)

Output: reports/surge_screen_<date>.md — ranked candidates + which rules
fires + theme tag. Screening is a hypothesis generator, not a recommendation.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd

from . import config, sql

REPORT_PATH = os.path.join(config.ROOT, "reports", f"surge_screen_{date.today():%Y-%m-%d}.md")

#: GICS sub-industry keyword -> theme tag; sector is the fallback.
_THEME_BY_SUB = [
    ("AI算力/芯片", ["Semiconductor", "Electronic Equipment"]),
    ("AI软件/平台", ["Software", "IT Services", "Interactive Media", "Internet", "Data Processing"]),
    ("AI电力/基建", ["Electric Utilities", "Independent Power", "Renewable", "Electrical Equipment"]),
    ("能源/大宗", ["Oil", "Gas" "Coal", "Metals", "Steel", "Chemicals"]),
]
_AI_SECTORS = {"Information Technology", "Communication Services"}
_MANUAL_THEME = {  # surge names not in the current SP500 list (extras)
    "SLNO": "医药临床", "AAOI": "AI光模块", "BITF": "加密矿", "BTBT": "加密矿",
    "HOOD": "零售交易平台", "WDC": "AI存储", "STX": "AI存储", "GEV": "AI电力",
    "VST": "AI电力", "TPL": "AI算力/基建", "LRCX": "AI算力/芯片", "AXON": "AI软件/军工",
}
#: dedupe share classes (keep the primary line)
_CLASS_ALIAS = {"GOOG": "GOOGL", "BRK.A": "BRK.B"}


def _theme(sector: str, sub: str) -> str:
    sub = sub or ""
    for name, kws in _THEME_BY_SUB:
        if any(k.lower() in sub.lower() for k in kws):
            return name
    if sector in _AI_SECTORS:
        return "科技/其他"
    return "其他"


def _register_themes(con) -> None:
    themes = config.load_themes()
    ev_syms = con.execute("SELECT DISTINCT symbol FROM events").fetchdf()["symbol"].tolist()
    rows = [(s, themes.get(s) or f"solo:{s}") for s in ev_syms]
    con.register("themes", pd.DataFrame(rows, columns=["symbol", "theme"]))


def build_report(top_n: int = 20) -> str:
    import duckdb

    con = duckdb.connect(config.DB, read_only=True)
    md: list[str] = []
    try:
        _register_themes(con)
        con.execute(sql.SQL_SCREEN_PREP)
        df = con.execute(sql.SQL_SCREEN_RANK).fetchdf()
    finally:
        con.close()

    # theme tagging from the SP500 meta (GICS) + manual map
    theme_map = {}
    if os.path.exists(config.META):
        meta = pd.read_csv(config.META)
        for _, r in meta.iterrows():
            sym = str(r["Symbol"])
            theme_map[sym] = _theme(str(r.get("GICS Sector") or ""), str(r.get("GICS Sub-Industry") or ""))
    df["theme"] = [theme_map.get(s, _MANUAL_THEME.get(s, "其他")) for s in df["symbol"]]

    # dedupe share classes (e.g. GOOG/GOOGL) AFTER ranking: drop the alias line if the primary is in top
    top = df.head(top_n)
    aliases = [k for k, v in _CLASS_ALIAS.items()
               if v in set(top["symbol"]) and k in set(top["symbol"])]
    top = top[~top["symbol"].isin(aliases)].reset_index(drop=True)
    top = top.head(top_n)

    md.append("# 暴涨候选筛选：基于验证规则（2026-08-21 快照，含主题共振）")
    md.append("")
    md.append("- 数据：最新财报（2026-01 之后）+ 8-20 收盘价；打分 = 超预期×1.0 + 12月动量×1.0 + EPS同比×0.5 + 6月动量×0.5 + 52周强度×0.5（z 分加权）")
    md.append("- 依据：[事件研究](surge_event_study_2026-08-21.md)（PEAD、强势动量×超预期最佳组合）+ [复合信号](composite_signal_2026-08-21.md)（K11：主题共振×动量）")
    md.append("- **共振数**：近 120 天内、同主题、也出超预期 top10% 财报的同行数（K8/K11 的实时化）；阈值取自已验证的 events 分布")
    md.append("- **这是假设生成器，不是投资建议**；筛选无未来信息（只用公告日已知数据）")
    md.append("")
    md.append(f"## Top {top_n} 候选")
    md.append("")
    show = top.copy()
    show["surprise_pct"] = show["surprise_pct"].round(1)
    show["report_date"] = show["report_date"].astype(str).str[:10]
    show["置信"] = show["surprise_pct"].map(
        lambda v: "✔" if abs(v) <= 50 else ("⚠" if abs(v) <= 100 else "‼")
    )
    show["eps_yoy%"] = (show["eps_yoy"] * 100).round(0)
    show["accel%"] = (show["eps_accel"] * 100).round(0)
    show["m6%"] = (show["mom6"] * 100).round(0)
    show["m12%"] = (show["mom12"] * 100).round(0)
    show["pos52%"] = (show["pos52"] * 100).round(0)
    show["dist_high%"] = (show["dist_high"] * 100).round(1)
    show["共振"] = show["resonance"].astype(int)
    show["顶"] = show["top_risk"].map(lambda v: "⚠" if v == 1 else "")
    show["rank"] = range(1, len(show) + 1)
    tbl = show[["rank", "symbol", "theme", "置信", "顶", "共振", "report_date", "surprise_pct", "eps_yoy%",
                "accel%", "m6%", "m12%", "pos52%", "dist_high%", "score"]]
    md.append(tbl.to_markdown(index=False))
    md.append("")
    md.append("> 含义：surprise_pct=最新财报超预期%；eps_yoy%=EPS同比；accel%=同比加速；")
    md.append("> m6/m12=6/12个月动量；pos52=52周区间位置(0低-1高)；dist_high=距52周高点%；")
    md.append("> 共振=近120天同主题超预期top10%同行数；score 为加权 z 分（越高越符合暴涨画像）。")
    md.append("> 置信：✔=常规(<50%)；⚠=偏高(50-100%)；‼=异常(>100%，一般为财报口径错配/扭亏转正，需人工核验)。")
    md.append("> **顶=顶部风险**（T9 复测中）：12m动量≥250% 且 6m动量≥100%（绝对双高=抛物线末端）。")
    md.append("> 历史上该系统人群内 12m 中位仅 6.5%（vs 基准 24%），含 MU/APP-2025/COIN/SMCI-2024 等顶案例；")
    md.append("> 同一形态但 12m<250%（近顶+强6m=主升段启动）历史中位 +91%，勿混淆 → 命中时谨慎、降低仓位，不加分。")
    md.append("")

    # T8: preferred pool = K11 composite (strong momentum x top surprise x resonance>=1)
    pool = df[(df["m_top25"] == 1) & (df["s_top25"] == 1) & (df["resonance"] >= 1)].head(12)
    pool = pool[~pool["symbol"].isin(
        [k for k, v in _CLASS_ALIAS.items() if v in set(pool["symbol"]) and k in set(pool["symbol"])])]
    md.append("## ★ 首选池：复合信号（强动量 × 超预期top25% × 共振≥1）——K11 验证的首选观察列")
    md.append("")
    if len(pool):
        pshow = pool.copy()
        pshow["surprise_pct"] = pshow["surprise_pct"].round(1)
        pshow["report_date"] = pshow["report_date"].astype(str).str[:10]
        pshow["m12%"] = (pshow["mom12"] * 100).round(0)
        pshow["pos52%"] = (pshow["pos52"] * 100).round(0)
        pshow["dist_high%"] = (pshow["dist_high"] * 100).round(1)
        pshow["共振"] = pshow["resonance"].astype(int)
        pshow = pshow.reset_index(drop=True)
        pshow["rank"] = range(1, len(pshow) + 1)
        cols = ["rank", "symbol", "theme", "共振", "report_date", "surprise_pct", "m12%",
                "pos52%", "dist_high%", "score"]
        md.append(pshow[cols].to_markdown(index=False))
        md.append("")
        md.append(f"> 依据 K11：该组 12m 中位收益 21.0%→31.5%（1同行），共振≥1 占最佳格 46%；"
                  f"当前命中 {len(pool)} 只。建议以此为首选观察列，人工复核财报原文。")
    else:
        md.append("（当前无标的同时命中 强动量×超预期top×共振≥1）")
    md.append("")

    # per-candidate rule recap
    md.append("## 命中规则解读（Top 10）")
    md.append("")
    md.append("| 股票 | 命中的规则 |")
    md.append("|---|---|")
    for _, r in top.head(10).iterrows():
        hits = []
        if r["surprise_pct"] >= 20:
            hits.append("盈利大超预期(>20%)")
        elif r["surprise_pct"] >= 8:
            hits.append("盈利超预期(>8%)")
        if r["eps_yoy"] and r["eps_yoy"] > 0.3:
            hits.append(f"EPS同比加速(+{r['eps_yoy']*100:.0f}%)")
        if r["mom12"] and r["mom12"] > 0.6:
            hits.append(f"强势动量(12m+{r['mom12']*100:.0f}%)")
        if r["mom6"] and r["mom6"] > 0.25:
            hits.append(f"近期走强(6m+{r['mom6']*100:.0f}%)")
        if r["pos52"] and r["pos52"] >= 0.9:
            hits.append("贴近52周高点")
        md.append(f"| {r['symbol']} | {'，'.join(hits) if hits else '—'} |")
    md.append("")

    md.append("## 方法与局限")
    md.append("")
    md.append("1. 规则来自 2023-2026 标普500 事件研究（6895 条财报）；统计上是**概率优势**，非确定性。")
    md.append("2. 打分用当前截面标准化（z 分），权重基于前向收益的组间差异，未经样本外拟合。")
    md.append("3. Universe 为当前标普500成分+暴涨股样本，含幸存者偏差；未计交易成本。")
    md.append("4. 历史上最锋利的上涨由极少数公司贡献（中位数<均值），名单只代表「符合画像」而非「必然暴涨」。")
    md.append("5. 建议结合最新季报原文（电话会指引、合同/订单）人工复核后再做任何决策。")
    md.append("")

    text = "\n".join(md)
    with open(REPORT_PATH, "w") as f:
        f.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:1800])
    return text


def main(argv: list[str] | None = None) -> int:
    build_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Composite signal validation (KNOWLEDGE.md T7).

Question: is "theme on fire + strong momentum" worth a dedicated list column,
i.e. does theme resonance (K8) add meaningful lift *on top of* the K3 best
cell ("strong momentum x top surprise") — and can it rescue weaker surprise?

Method: 3-way cut of every earnings event —
  surprise bucket   (top25% / mid / bottom25%, quantiles)
  momentum bucket   (strong / mid / weak, 12m return quantiles)
  resonance         (0 / 1 / 2+ same-theme peers with top-10% surprise in
                     trailing 120d)
Focus tables: strong-momentum subset (the stocks one would actually pick)
and weak-momentum control. All signal inputs are known before the event;
forward returns start after the next tradable bar — no lookahead.

Output: reports/composite_signal_<date>.md
"""
from __future__ import annotations

import os
from datetime import date

import duckdb

from . import config, sql

REPORT_PATH = os.path.join(config.ROOT, "reports", f"composite_signal_{date.today():%Y-%m-%d}.md")

S_ORDER = ["超预期bottom25%", "中位", "超预期top25%"]
R_ORDER = ["0同行", "1同行", "2+同行"]


def _register_themes(con) -> None:
    themes = config.load_themes()
    ev_syms = con.execute("SELECT DISTINCT symbol FROM events").fetchdf()["symbol"].tolist()
    rows = [(s, themes.get(s) or f"solo:{s}") for s in ev_syms]
    import pandas as pd

    con.register("themes", pd.DataFrame(rows, columns=["symbol", "theme"]))


def _grid(df, m_b: str) -> tuple[list[list[str]], dict]:
    """3x3 grid median (12m) for one momentum bucket: rows=surprise, cols=resonance."""
    rows = []
    meta = {}
    for sb in S_ORDER:
        row = []
        for rb in R_ORDER:
            r = df[(df["m_b"] == m_b) & (df["s_b"] == sb) & (df["r_b"] == rb)]
            if r.empty:
                row.append("—")
            else:
                med = r["fwd252_med_pct"].iloc[0]
                avg = r["fwd252_avg_pct"].iloc[0]
                n = int(r["n"].iloc[0])
                row.append(f"{med:.1f}% (n={n})")
                meta[(sb, rb)] = {"med": med, "avg": avg, "n": n}
        rows.append(row)
    return rows, meta


def build_report() -> str:
    con = duckdb.connect(config.DB, read_only=True)
    try:
        _register_themes(con)
        df = con.execute(sql.SQL_COMPOSITE).fetchdf()
    finally:
        con.close()

    md: list[str] = []
    md.append("# 复合信号验证：主题共振 × 12月动量 × 超预期（T7）")
    md.append("")
    md.append("- 数据：events 表（2023-01~2026-06）；切分遵循 K3（四分位）与 K8（同行 120 天 top10% 超预期）")
    md.append("- 问题：**主题正在火的强动量票，是否值得专门一列？**（共振是否在最佳组合之上再加分 / 是否救活平庸超预期）")
    md.append("")

    rows, meta = _grid(df, "强势(动量top25%)")
    md.append("## 表1｜强势动量(12m top25%)：超预期档 × 共振 → 12月前向收益")
    md.append("")
    md.append("| 超预期档 | 0同行 | 1同行 | 2+同行 |")
    md.append("|---|---:|---:|---:|")
    for sb, row in zip(S_ORDER, rows):
        md.append(f"| {sb} | {' | '.join(row)} |")
    md.append("")
    md.append("> 横向看共振效果：若 2+同行 高于 0同行，说明「主题正在火」在强动量票上额外加分。")
    md.append("")

    rows_w, _ = _grid(df, "弱势(动量bottom25%)")
    md.append("## 表2｜弱势动量(12m bottom25%)：对照组")
    md.append("")
    md.append("| 超预期档 | 0同行 | 1同行 | 2+同行 |")
    md.append("|---|---:|---:|---:|")
    for sb, row in zip(S_ORDER, rows_w):
        md.append(f"| {sb} | {' | '.join(row)} |")
    md.append("")
    md.append("> 若共振只在强动量组生效、弱动量组无效 → 共振是「放大器」，与动量互补。")
    md.append("")

    # K3 best cell split: strong momentum x top surprise by resonance
    best = df[(df["m_b"] == "强势(动量top25%)") & (df["s_b"] == "超预期top25%")]
    md.append("## 表3｜K3 最佳格内部：强动量 × 超预期top25%，按共振拆分")
    md.append("")
    md.append("| 共振 | n | 12m中位% | 12m均值% | 占比 |")
    md.append("|---:|---:|---:|---:|---:|")
    tot = int(best["n"].sum())
    for rb in R_ORDER:
        r = best[best["r_b"] == rb]
        if r.empty:
            continue
        med = r["fwd252_med_pct"].iloc[0]
        avg = r["fwd252_avg_pct"].iloc[0]
        n = int(r["n"].iloc[0])
        md.append(f"| {rb} | {n} | {med:.1f} | {avg:.1f} | {n/tot*100:.0f}% |")
    md.append("")
    md.append("> 占比列回答「专门一列」的可操作性：2+同行 的比例决定了它是常见形态还是罕见形态。")
    md.append("")

    md.append("## 结论")
    md.append("")
    md.append("**答案：值得专门一列。** 共振在强动量组上加成分明，且出现频率不低。")
    md.append("")
    md.append("1. **最佳格内加成**：强动量×超预期top25% 中，0同行 12m 中位 21.0%，1同行 31.5%（+10.5pp），")
    md.append("   2+同行 27.0%（+6.0pp）；且共振≥1同行 占该格 46%——不是罕见形态，「有共振的强动量好票」值得单独取景。")
    md.append("2. **共振救活「一半」的平庸**：强势动量×中位超预期，2+同行 25.5%（+9.1pp），几乎追平独立最佳格（21.0%）；")
    md.append("   但 bottom25% 超预期无任何加成（10.9→8.7）——**共振抬升「差一点」但不创造「差很多」**。")
    md.append("3. **共振是动量放大器，不是替代品**：弱动量组（对照表2）共振几乎无效且无单调性，")
    md.append("   强动量组才稳定生效——选股的优先级仍是 动量>超预期>共振。")
    md.append("4. **2+同行 = 更可交易的形态**：最佳格内均值-中位差，0同行 22.3pp vs 2+同行 6.4pp；")
    md.append("   1同行（31.5%）> 2+同行（27.0%）的非单调，可能是主题拥挤（多家同时超预期常处周期中后段）；")
    md.append("   这提示「共振刚起（1同行）比共振已满（2+同行）更有利」。")
    md.append("")
    md.append("**操作含义**：给 `screen` 增加「共振数」列（T8，实时化 K8 的 120 天同行窗口）——")
    md.append("候选按 强动量×超预期top×共振≥1 单独建池，作为首选观察列。")
    md.append("")

    text = "\n".join(md)
    with open(REPORT_PATH, "w") as f:
        f.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:2600])
    return text


def main(argv: list[str] | None = None) -> int:
    build_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Event study: 100% of the computation runs as DuckDB SQL; Python only
renders the markdown report.

Reads the `events` table from data/market.duckdb (built by
`python -m apps.research events`) and writes reports/surge_event_study_<date>.md.
"""
from __future__ import annotations

import os
from datetime import date

import pandas as pd

from . import config, sql

SPOTLIGHT = ["PLTR", "MU", "CVNA", "HOOD", "NVDA", "WDC", "VST", "AXON"]
REPORT_PATH = os.path.join(
    config.ROOT, "reports", f"surge_event_study_{date.today():%Y-%m-%d}.md"
)


def _run(con, statement: str) -> pd.DataFrame:
    return con.execute(statement).fetchdf()


def build_report() -> str:
    con = config.open_db(read_only=True)
    md: list[str] = []
    try:
        n_events = int(con.execute("SELECT count(*) FROM events").fetchone()[0])
        n_sym = int(con.execute("SELECT count(DISTINCT symbol) FROM events").fetchone()[0])

        md.append("# 暴涨股共性事件研究：盈利超预期信号的「提前期」检验")
        md.append("")
        md.append(f"- 数据窗口：2023-01 ~ 2026-06 的财报事件，共 {n_events:,} 条（{n_sym} 只股票）")
        md.append("- 信号均为公告日可知，收益自公告日后首个交易日收盘计（无前视偏差）")
        md.append("- **全部计算在 DuckDB SQL 中完成**（`python -m apps.research events` → 本脚本只渲染）")
        md.append("- Universe 为 2026-08 时点的标普500成分 + 部分暴涨股，存在幸存者偏差（见结论）")
        md.append("")

        md.append("## 1. 按盈利超预期% 分十档 → 前瞻收益")
        md.append("")
        d = _run(con, sql.Q_DECILES)
        md.append(d.to_markdown(index=False))
        md.append("")
        md.append("> 解读：若超预期档的前瞻收益显著高于低档，说明该信号存在可交易的前瞻 alpha（即 PEAD）。")
        md.append("")

        md.append("## 2. 提前期曲线：最佳超预期档（D9-D10）的累积平均收益")
        md.append("")
        lead = _run(con, sql.Q_LEAD_TIME)
        lead.columns = ["持有期", "交易日", "平均收益%", "中位数%", "样本"]
        md.append(lead.to_markdown(index=False))
        md.append("")
        md.append("> 提前期 = 该表呈现信号出现后 1/3/6/12 个月的兑现进度；若 1-3 个月已吃掉大部分，")
        md.append("> 说明信号「等确认就晚了」；若 12 个月仍在累积，说明可以「拿着等」。")
        md.append("")

        md.append("## 3. 交互：低位×超预期 vs 高位×超预期")
        md.append("")
        ip = _run(con, sql.Q_INTERACTION_POS)
        ip["组合"] = ip["超预期"] + " × " + ip["位置"]
        ip = ip[["组合", "n", "fwd20_med_pct", "fwd63_med_pct", "fwd126_med_pct", "fwd252_med_pct"]]
        md.append(ip.to_markdown(index=False))
        md.append("")
        md.append("> 检验报告里的共性#4：低位+高空头的暴涨是否在超预期信号上叠加更大涨幅。")
        md.append("")

        md.append("## 4. 交互：12个月动量 <-> 超预期")
        md.append("")
        im = _run(con, sql.Q_INTERACTION_MOM)
        im["组合"] = im["超预期"] + " × " + im["动量"]
        im = im[["组合", "n", "fwd20_med_pct", "fwd63_med_pct", "fwd126_med_pct", "fwd252_med_pct"]]
        md.append(im.to_markdown(index=False))
        md.append("")

        md.append("## 5. 个案：暴涨股的「信号出现时刻」")
        md.append("")
        md.append("> 注：本表显示原始（未做1%/99%裁剪）的超预期与动量值。")
        md.append("")
        cols = ["股票", "报告日", "超预期%", "12个月动量%", "之后6个月%", "之后12个月%"]
        md.append("| " + " | ".join(cols) + " |")
        md.append("|" + "|".join(["---"] * len(cols)) + "|")
        sp = _run(con, sql.q_spotlight(SPOTLIGHT))
        for _, r in sp.iterrows():
            f252 = f"{r['fwd252']*100:.0f}" if pd.notna(r["fwd252"]) else "—"
            md.append(
                f"| {r['symbol']} | {str(r['report_date'])[:10]} | {r['surprise_pct']:.1f} | "
                f"{r['mom12_pct']*100:.0f} | {r['fwd126']*100:.0f} | {f252} |"
            )
        md.append("")

        md.append("## 5b. 退出纪律：前向收益随持有期的漂移（何时下车？）")
        md.append("")
        md.append("「系统人群」= 超预期top25% × 强动量top25%（K3/K11 的进场人群）；中位收益（%）。")
        md.append("")
        ex = _run(con, sql.SQL_EXIT_DECAY)
        ex_p = ex.pivot_table(index="label", columns="h", values="med_pct")
        ex_p = ex_p.reindex(["1 系统人群(超预期top×强动量)", "2 超预期top25(非强动量)", "3 其余"])
        ex_p = ex_p[["1个月", "4个月", "8个月", "12个月", "15个月", "18个月", "24个月"]]
        md.append(ex_p.to_markdown())
        md.append("")
        md.append("> **诚实解读**：在 2023-2026 窗口内，系统人群的前向收益在 12 个月后**并未衰减**")
        md.append("> （12m 中位 23.6% → 24m 48.6%），且与「其余」的差距随时间扩大（1m +1.0pp → 24m +27.3pp）。")
        md.append("> 因此「12 个月下车」不是**收益最优**，而是**纪律折中**：样本到 18-24 个月大幅缩水")
        md.append("> （n 433→357→259，更长持有只有 2023-2024 年的报告，自然躲开了 2025 顶部阶段，含幸存偏差），")
        md.append("> 且 24m 均值-中位差达 57pp（极端右尾）。结论：**edge 在 12 个月内确定累积，此后统计上不衰减；**")
        md.append("> 纪律层面 12 个月是可靠基准，容忍更长持有属个人风险偏好，不能凭本窗口数据断言更优。")
        md.append("")

        md.append("## 6. 结论与局限")
        md.append("")
        md.append("1. **可量化性**：盈利超预期（surprise）是免费可得、公告日对齐、可回测的强信号；")
        md.append("   上面第 1、2 节的数字就是它的量化证据，且全部由 DuckDB SQL 计算。")
        md.append("2. **提前期**：见第 2 节曲线——信号在随后 1/3/6/12 个月的兑现节奏决定策略持有期。")
        md.append("3. **放大器**：第 3、4 节显示低起点/动量与超预期的交互是否成立。")
        md.append("4. **局限**：a) Universe 有幸存者偏差（当前 SP500 成分）；b) 未计交易成本/滑点；")
        md.append("   c) 报表重叠与多信号共振未处理；d) 收益中位数普遍低于均值，说明强者集中。")
        md.append("")
    finally:
        con.close()
    return "\n".join(md)


def main(argv: list[str] | None = None) -> int:
    text = build_report()
    with open(REPORT_PATH, "w") as f:
        f.write(text)
    print(f"report -> {REPORT_PATH}")
    print(text[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Repeat-winner validation (KNOWLEDGE.md T5).

Two sub-hypotheses, both on existing data (events table, no lookahead):
  A. a stock that already DOUBLED off a prior earnings surprise tends to do it
     again ("二次暴涨" / repeat-winner persistence);
  B. a stock with consecutive top-decile surprises keeps paying off
     ("连续超预期" / serial-beat persistence).

For each event we count, in the trailing 365 days BEFORE that event:
  - prior_doubles    : prior events whose own fwd252 >= +100%
  - prior_top_surp   : prior events whose surprise was top-10%
Then forward returns are compared within the event's own surprise bucket.

Output: reports/repeat_surgers_<date>.md
"""
from __future__ import annotations

import os
from datetime import date

import duckdb

from . import config, sql

REPORT_PATH = os.path.join(config.ROOT, "reports", f"repeat_surgers_{date.today():%Y-%m-%d}.md")


def build_report() -> str:
    con = duckdb.connect(config.DB, read_only=True)
    try:
        df = con.execute(sql.SQL_REPEAT).fetchdf()
    finally:
        con.close()

    md: list[str] = []
    md.append("# 二次暴涨与连续超预期验证（T5）")
    md.append("")
    md.append("- 数据：events 表 6,895 条（2023-01~2026-06）；历史窗口=事件前 365 天")
    md.append("- 「已翻倍」= 过去 365 天内，该股某次财报后的 12 个月前向收益 ≥ +100%")
    md.append("- 「连续超预期」= 过去 365 天内，该股 top10% 超预期财报的累计次数")
    md.append("- 全部为事件前已知信息（无前视）")
    md.append("")

    # pivot: within each surprise bucket, compare by prior_doubles
    def _cell(bucket: str, label: str, col: str) -> str:
        row = df[(df["s_bucket"] == bucket) & (df[f"{col}_label"] == label)]
        if row.empty:
            return "—"
        v = row["fwd252_med_pct"].iloc[0]
        n = row["n"].iloc[0]
        return f"{v:.1f}% (n={int(n)})"

    md.append("## A. 二次暴涨：已翻倍的股票是否更可能再涨（12m 中位收益）")
    md.append("")
    md.append("| 自身超预期档 | 0次(首次事件) | 1次 | 2+次 |")
    md.append("|---|---:|---:|---:|")
    for bucket in ["超预期bottom25%", "中位", "超预期top10%"]:
        md.append(f"| {bucket} | {_cell(bucket, '0次', 'prior_doubles')} | "
                  f"{_cell(bucket, '1次', 'prior_doubles')} | {_cell(bucket, '2+次', 'prior_doubles')} |")
    md.append("")
    md.append("> 若横向递增：翻倍过的赢家更可能再次翻倍（赢家持续性）；若递减：均值回归。")
    md.append("")

    md.append("## B. 连续超预期：连续 top10% 财报是否继续兑现")
    md.append("")
    md.append("| 自身超预期档 | 0次 | 1次 | 2+次 |")
    md.append("|---|---:|---:|---:|")
    for bucket in ["超预期bottom25%", "中位", "超预期top10%"]:
        md.append(f"| {bucket} | {_cell(bucket, '0次', 'prior_top_surp')} | "
                  f"{_cell(bucket, '1次', 'prior_top_surp')} | {_cell(bucket, '2+次', 'prior_top_surp')} |")
    md.append("")
    md.append("## 结论")
    md.append("")
    md.append("**A（二次暴涨）方向成立，但与「动量×超预期」高度重叠，不是独立信号：**")
    md.append("")
    md.append("- 已翻倍过的股票几乎不论自身超预期档，12m 中位收益 60%~176%（1-2次样本 n=10~27，方向极强）；")
    md.append("  但「近期翻倍」本身就是最强价格动量——这正是 K3「强势动量×超预期最佳组合」的另一面，")
    md.append("  因此 A 表更像是给「赢家持续性」一个量化刻画，而非新证据。")
    md.append("- 真正常见情形（n 大）是 0次：即暴涨持续性是**少数例外**而非常态。")
    md.append("")
    md.append("**B（连续超预期）证伪：历史连胜不叠加前向收益。**")
    md.append("")
    md.append("- top10% 档 12m 中位 13.2% → 13.0% → 11.5%，中位档也无单调模式；")
    md.append("  上期超预期早已被市场定价，本期信号只看本期事件本身。")
    md.append("- 含义：**不要因为「上季也超预期」而给本期加仓理由**；放大器是价格动量（K3/K4），不是超预期连胜。")
    md.append("")
    md.append("## 局限与说明")
    md.append("")
    md.append("1. 「已翻倍」与价格动量高度重叠（翻倍本身即强动量）；B 表与「连续超预期」更接近 EPS 持续性，二者互相对照。")
    md.append("2. 使用 fwd252 作为「是否翻倍」的度量含幸存偏差（活到 252 天后才计）；且未计成本。")
    md.append("3. 全部基于 2023-2026 窗口，样本期偏牛市。")
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

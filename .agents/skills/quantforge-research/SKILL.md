---
name: quantforge-research
version: 1.0.0
description: "QuantForge 暴涨股研究数据流水线与事件研究操作手册。当任务涉及 market.duckdb / DuckDB 仓库、earnings & prices 数据、事件研究（event study / PEAD / 盈利超预期）、暴涨候选筛选（surge screen）、数据快照或复现研究报告时使用。给出统一 CLI（python -m apps.research / scripts/research.py）的全部命令、标准工作流、无前视纪律与常见排错。"
---

# QuantForge Research 操作手册

**用途**：让新会话不从头摸索——直接按本手册驱动 `apps/research/` 的完整流水线，
应用本仓库已**验证过**的研究规则，并避开已知数据陷阱。

## 1. 何时使用

- 用户提到"研究/事件研究/PEAD/盈利超预期/暴涨股/暴涨候选/筛选/回测信号"。
- 需要查询或重建 `data/market.duckdb`（prices / earnings / events / catalog）。
- 需要下载或刷新 earnings / prices 数据，或保存/恢复数据快照。
- 需要复现或验证 `reports/` 下的研究报告。

不适用：策略回测/风控/下单（那是 `quantforge.strategy` 的活）；本技能只管**研究数据层**。
- 每日自动：systemd user timer `research-daily.timer`（每天 23:30 CST），编排见 `apps/research/daily.py`；
  邮件推送凭证 `.keys/.secrets.toml [SMTP]`（Gmail 用应用专用密码），`research email test` 验证。

## 2. 环境与路径

- 仓库根：`/home/pzheng46/QuantForge`；Python 解释器：`.venv/bin/python`（依赖见
  `apps/research/requirements.txt`：duckdb / pandas / pyarrow / tabulate / yfinance）。
- 数据分层：原始层 `data/earnings|prices/*.csv` + `data/crypto/*` + `data/options/*`
  → 查询层 `data/market.duckdb`（prices/earnings/events + crypto_* + option_chains）
  → 备份层 `~/.quantforge/data-snapshots/*.tar.gz`。
- **统一入口**：`python -m apps.research <命令>`（或 `scripts/research.py <命令>`）。

## 3. 命令速查

```bash
python -m apps.research download earnings [--workers 6] [--force] [--symbols A,B]
python -m apps.research download prices    [--chunk 40] [--years 6y]
python -m apps.research download crypto   [--force] [--spots A,B] [--perps A,B]   # bitget 公开接口
python -m apps.research import       # raw -> duckdb（含 SQL 构建 events，幂等重建）
python -m apps.research import-crypto      # crypto OHLCV/资金费率/OI -> duckdb
python -m apps.research import-options     # option chain snapshots -> duckdb
python -m apps.research events       # 只重建 events 表（不重导 raw）
python -m apps.research study        # 事件研究 -> reports/surge_event_study_<date>.md
python -m apps.research screen       # 暴涨候选筛选 + 共振列 + ★首选池(K11/T8) + 顶列(T9) -> reports/surge_screen_<date>.md
python -m apps.research resonance    # 主题共振验证(T3/K8) -> reports/theme_resonance_<date>.md
python -m apps.research repeats      # 二次暴涨/连续超预期验证(T5/K9,K10) -> reports/repeat_surgers_<date>.md
python -m apps.research composite    # 共振×动量复合信号(T7/K11) -> reports/composite_signal_<date>.md
python -m apps.research crypto       # 加密研究：动量/趋势/波动/资金费率/OI -> reports/crypto_research_<date>.md
python -m apps.research options      # 期权链研究：ATM IV/skew/期限结构（Schwab 实时链）-> reports/options_research_<date>.md
python -m apps.research technical    # 多资产纯价格/技术面（美股+加密同一镜头）-> reports/technical_screen_<date>.md
python -m apps.research daily [--email|--no-email]  # 每日自动：crypto→options→technical→(邮件)
python -m apps.research email config|test  # 邮件配置状态 / 发测试信（[SMTP] 在 .keys/.secrets.toml）
python -m apps.research status       # 缓存+DB 体检（JSON）
python -m apps.research verify       # 完整性校验
python -m apps.research query "SQL"  # 只读查询
python -m apps.research manifest     # 重建下载清单
python -m apps.research snapshot --keep 5
python -m apps.research restore <snap.tar.gz>
```

## 4. 标准工作流

- **首次搭建**：`download earnings` → `download prices` → `import` → `study` → `screen`。
- **日常刷新**（已有缓存）：只跑 `events`（或需要时先 `download`），再 `study`/`screen`。
  **下载器幂等：已有文件绝不重复下载**，除非显式 `--force`。
- **复现报告**：一条命令链 `import → study`；数字与既有 `reports/` 对比验证（见§6）。
- **备份**：大改动前后 `snapshot --keep 5`。

## 5. 不可违背的纪律（违反即结论失效）

1. **无前视**：信号只用公告日可知数据；前瞻收益自公告日后**首个可交易 bar 收盘**起算。
2. **时区**：Yahoo 财报时间戳是美东；SQL 统一 `report_date - INTERVAL '5 hours'` 转本地日历日，
   否则盘后财报滚次日导致前向收益错位（D1）。
3. **SQL 单一来源**：所有事件/分析 SQL 只改 `apps/research/sql.py`，禁止散落临时 SQL。
4. **极端值**：分档/打分前先 1%/99% 裁剪再标准化。
5. **数据可信度**：报表超预期 >100% 多为口径错配（GOOGL/AMZN 曾出现 +214% 伪值），
   展示时必须带置信标记（✔/⚠/‼），>100% 一律标注需人工核验（D2）。
6. **历史长度**：用 12 个月动量/同比前确认标的 ≥300 根价格 bar，次新股/分拆易失真（D3）。

## 6. 已验证结论速览（完整证据见 apps/research/KNOWLEDGE.md）

- 盈利超预期 = 可量化的前瞻 alpha（PEAD）：top10% 档 12m 平均 +42.9% vs 底档 +25.3%。
- 信号提前期持续约 12 个月（1/3/6/12m 平均 +2.2/+6.8/+15.5/+40.2%）→ "拿了等"优于"追确认"。
  退出纪律（study 5b 节全样本）：系统人群前向中位 12/18/24m=+23.6/+35.8/+48.6%，12 个月后未衰减——
  "12 个月下车"是**纪律折中非收益最优**（更长持有样本躲开 2025 顶部、含幸存偏差）。
- **最佳组合 = 强动量(12m top25%) × 超预期(top25%)**：12m 中位 +23.9%。
- 52周高位×超预期 好于 低位×超预期（低位自带 ~15% 漂移但不叠加）。
- **主题共振成立（K8）**：同行 120 天内也超预期 → 中位档 12m 中位 10.3→17.0%（单调），top10% 档 +8pp；
  共振事件右尾小、更可交易。命令：`research resonance`。
- **二次暴涨方向成立但与动量重叠（K9）；连续超预期证伪（K10）**——上期超预期已被定价，
  历史连胜不加分。命令：`research repeats`。
- **复合信号（K11）**：强动量×超预期top 内，共振≥1 同行 让 12m 中位 +6~10pp（最佳格内 21.0→31.5%），
  共振≥1 占最佳格 46%；共振救「中位超预期」但不救 bottom25%；共振是动量放大器、非替代品。
  命令：`research composite`。已实时化进 `screen`：候选表带「共振」列，且有 ★首选池
  （强动量×超预期top×共振≥1，K11/T8），另带「顶」列标记顶部风险（T9 复测中：
  12m动量≥250% 且 6m≥100% 绝对双高 = 抛物线末端，历史 252d 中位 6.5% vs 24%；
  12m<250% 的近顶+强6m 反而是主升段启动，中位 91%，勿混淆 → 命中用降仓/不追而非禁买）。
- 收益右偏（中位<均值）：涨幅高度集中，结论只代表概率优势。

## 7. 常见排错

- `no database yet` → 先 `import` 建库。
- `study` 报 `to_markdown` 缺失 → 缺 `tabulate`，`uv pip install --python .venv/bin/python tabulate`。
- SP500 列表获取 403 → 用 GitHub `datasets/s-and-p-500-companies` 镜像（已固化在 config.py）。
- `verify` 提示少量 `too few rows`（如 FDXF/HONA/VMRK）属退市/新上市正常现象，非错误。

## 8. 维护本技能

- 本文件是"操作手册"：命令、工作流、纪律、排错 **沉淀在这里**。
- `apps/research/KNOWLEDGE.md` 是"证据库"：已验证发现、数据陷阱、待办。两者不重复：
  技能只提炼要点并链接过去；发现/陷阱变更时两边同步维护。
- 扩展为技能库：在 `.agents/skills/<新技能名>/SKILL.md` 新增目录+frontmatter
  （name / version / description，description 决定触发时机）即可；同层已有技能会自动被 DSH 扫描。
- 数据或结论重大变化（如窗口/Universe 变更）后：更新 KNOWLEDGE.md 状态列（复测中/已失效）并同步本文件。

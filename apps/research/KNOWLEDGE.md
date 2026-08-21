# QuantForge 研究知识库（KNOWLEDGE）

本文件沉淀事件研究/数据工程中积累的、**可复用**的经验。与一次性报告
（`reports/`）的区别：报告讲"这次发现了什么"，本文件讲"以后怎么用"。

## 维护规则

- **条目结构**：每条发现 = 编号 + 日期 + 结论（一句话）+ 证据（数字/报告链接）
  + 复现（命令或 SQL）+ 状态 + 适用范围/局限。
- **状态生命周期**：`已验证`（有统计证据）→ `复测中`（数据刷新后重跑验证）
  → `已失效`（标记时间与原因）。每季度或每次重大数据更新后复查一遍。
- **代码即经验**：凡能写成规则的（打分权重、阈值、信号定义），一律进代码
  （`sql.py` / `config.py`），本文件只写"为什么"和"怎么用"，不重复写实现。
  经验和代码打架时，以代码为准并更新本文件。

---

## A. 已验证的量化发现（有事件研究证据）

| # | 结论 | 证据 | 复现 | 状态 |
|---|---|---|---|---|
| K1 | 盈利超预期是免费可得、可量化的前瞻 alpha（PEAD） | 超预期 top10% 档 12m 平均 +42.9%，底档 +25.3%，中位档 ~+16% | `research study`（第1节） | 已验证 |
| K2 | 信号的"提前期"持续约 12 个月，不是等确认就晚。**退出纪律（5b 节全样本）**：系统人群（超预期top×强动量）前向中位 1/4/8/12/18/24m = +1.8/+7.6/+17.4/+23.6/+35.8/+48.6%——12 个月后**未衰减**，与其余人群差距随时间扩大（+1.0→+27.3pp）；"12 个月下车"是纪律折中而非收益最优（更长持有样本只有 2023-24 报告、躲开 2025 顶、含幸存偏差） | `research study`（第2节 + 5b 节） | 已验证 |
| K3 | 最佳组合=强动量×超预期（12m 中位 +23.9%），弱动量×超预期不叠加 | 交互表第4节：强势top25%×超预期top25% vs 其余格 | `research study`（第4节） | 已验证 |
| K4 | 52周高位×超预期 好于 低位×超预期（+19.5% vs +14.7% 中位）；低位自带 ~15% 漂移但不叠加 | 交互表第3节 | `research study`（第3节） | 已验证 |
| K5 | 收益右偏（中位<均值）；涨幅高度集中在极少数公司 | 各档 均值≈1.5~2×中位 | `research study` | 已验证 |
| K6 | 同一主题多只股票同时满足画像（"主题共振"）时，集群信号强于单只 | 2026-08 筛查：AI 存储/算力 5 只同榜 | `research screen` | 复测中→已验证 |
| K7 | 个案验证"暴涨股=信号出现时刻"成立（PLTR/HOOD/MU），例外为逼空型（CVNA 负超预期仍暴涨） | 个案表第5节 + 主报告 | `research study`（第5节） | 已验证 |
| K8 | **主题共振成立**：同行业其他股票 120 天内也超预期 top10% → 前向收益更高。中位档 12m 中位单调 10.3%→17.0%（2+同行反超独立 top10% 的 14.4%）；top10% 档 1同行 +8.0pp；共振事件中位-均值差小（更可交易） | `reports/theme_resonance_2026-08-21.md` | `research resonance` | 已验证（T3 转正） |
| K9 | **二次暴涨（已翻倍赢家）方向成立但与动量重叠**：已翻倍者的后续 12m 中位 60~176%（所有档位），但样本小（n≤27）且本质=强动量，非独立信号 | `reports/repeat_surgers_2026-08-21.md` 表A | `research repeats` | 初步成立（T5-A） |
| K10 | **连续超预期证伪**：历史上连续 top10% 财报不叠加前向收益（top10% 档 13.2%→13.0%→11.5%）——上期超预期已被定价，放大器是价格动量（K3/K4）而非超预期连胜 | `reports/repeat_surgers_2026-08-21.md` 表B | `research repeats` | 证伪（T5-B） |
| K11 | **共振×动量复合信号（T7）**：强动量×超预期top25% 中，0同行 12m 中位 21.0% → 1同行 31.5%（+10.5pp）→ 2+同行 27.0%；共振≥1 占最佳格 46%；2+同行 均值-中位差仅 6.4pp（更可交易）。共振救活「中位超预期」（+9.1pp 至 25.5%）但不救 bottom25%；弱动量组对照无效 → **共振是动量放大器非替代品** | `reports/composite_signal_2026-08-21.md` | `research composite` | 已验证（T7 转正） |

范围/局限：全为当前 SP500 成分 + 暴涨股样本（**幸存者偏差**）；未计交易成本/滑点。

## B. 数据工程经验（踩过的坑）

| # | 教训 | 处理办法 | 出处 |
|---|---|---|---|
| D1 | Yahoo 财报时间戳是美东时间，直接当 UTC 会把盘后财报滚到次日 → 前向收益错位 | 统一 `report_date - 5h` 转本地日历日；修复后与旧版 0 差异 | `sql.py` + `data/README.md` |
| D2 | 超大盘股 surprise>100% 是口径错配/估测缺失伪值（GOOGL +214%、AMZN +215%） | 筛查加"置信"标记（✔/⚠/‼），>100% 需人工核验 | `screen.py` |
| D3 | 次新股/分拆（SNDK）的 12m 动量、EPS 同比因基期小/历史短而失真 | 筛查要求 ≥300 根价格 bar 的历史；分拆需上下文 | `sql.py` SQL_SCREEN_PREP |
| D4 | Wikipedia SP500 列表 403 | 改用 GitHub `datasets/s-and-p-500-companies` 镜像（含 GICS 行业列） | `config.py` |
| D5 | pandas `parse_dates=True` 对带时区索引无效 | 显式 `pd.to_datetime(..., utc=True)` 再 `tz_localize(None)` | `warehouse.py` |
| D6 | DuckDB `GROUP BY` 不能含窗口函数；`ntile` 遇重复值会拆桶（曾产生重复"中位"行） | 子查询包裹窗口函数；按**标签列**而非桶号 GROUP BY | `sql.py` |
| D7 | 下载幂等防冗余：**已有文件绝不重复下载** | 下载器记录 manifest + 存在性跳过；`--force` 才重下 | `download.py` |

## C. 方法论（可迁移到任何后续研究）

- **无前视基准**：信号只用公告日可知数据；前向收益自公告日后首个可交易 bar 收盘起算。
- **极端值处理**：先 1%/99% 裁剪再 z 分标准化，避免少数异常值主导打分。
- **三层数据架构**：原始层（CSV，事实）→ 查询层（DuckDB SQL，events 表 100% SQL 构建）
  → 备份层（tar.gz 快照，可复现）。
- **SQL 单一来源**：所有 SQL 集中在 `sql.py`，代码/报告引用同一份 → 不会出现"两版真相"。
- **一条命令全链路**：`import` → `events` → `study`/`screen`，数据刷新即可整体重算复验。
- **事件表字段纪律**：报告日=美东本地日；fwd* 列名即交易日数，便于新研究直接复用。

## D. 待验证 / 待办（下阶段候选）

| # | 事项 | 说明 | 需要的资源 |
|---|---|---|---|
| T1 | 空头拥挤/机构持仓第二层信号 | 与超预期交互是否再叠加 | Short interest API / 13F 数据 |
| T2 | 季度收入 `rev_yoy` 信号 | 代码钩子已留（`financials/`），未启用 | AlphaVantage/FMP key（免费额度） |
| T4 | 交易成本/滑点敏感性 | 事件研究收益均为毛收益 | 假设费用曲线 |
| T6 | 财报季事件密度 | 每季度财报期前可做"即将发布"提前预警 | `screen` 扩展报告日字段 |
| T9 | **顶部回避**（复测中）：**12m动量≥250% 且 6m动量≥100%（绝对双高）=抛物线末端**，系统人群内 252d 中位仅 6.5%（n=12，vs 基准 24%；含 MU-2026-06/APP-2025-02/COIN-2024-05/SMCI-2024-04）。**关键分水岭**：同形态但 12m<250%（近顶+强6m=主升段启动）历史中位 +91%（VRT/LITE/PLTR-2023-11）——顶部 vs 主升就看 12m 累计高度。局限：PLTR-2025-08（高位横盘再突破）两种标记都漏，需共振≥3+仓位止损兜底。已实现为 screen「顶」列（T9v2，绝对双高） | 更多数据窗口 / 全 top10% 样本扩大检验 |

> 已完成：T3（主题共振→K8）、T5（二次暴涨→K9 / 连续超预期→K10 证伪）、T7（共振×动量复合→K11）、T8（共振实时化进 `screen`：候选加「共振」列 + ★首选池=强动量×超预期top×共振≥1）。

---

## E. 多资产研究层（2026-08-21 第一版）

架构目标：把期权/加密/常规交易都纳入 `apps/research/`，与美股事件研究同层；
**分析下沉 Research，实盘下单留守 dashboard/risk**（Option 老模块的窄耦合设计问题）。

**新资产数据层（DuckDB 新表）**
- `crypto_ohlcv(symbol, ts, o/h/l/c, volume)` / `crypto_funding(symbol, ts, value)` /
  `crypto_oi(symbol, ts, value)`：bitget 公开接口，原始 CSV 在 `data/crypto/`。
- `option_chains(snapshot_at, ticker, symbol, expiration, strike, "right", bid, ask, last,
  iv, delta, gamma, theta, vega, open_interest, volume)`：Schwab 实时链快照，CSV 在 `data/options/`。

**三资产第一版报告（命令）**
- `research crypto` → `reports/crypto_research_*.md`：动量阶梯(7/30/90/360d) + MA 结构 +
  年化波动 + 30d 资金费率 + OI 日采样 + 永续-现货基差。
- `research options` → `reports/options_research_*.md`：实时链 ATM IV / 25Δ call skew /
  期限结构（近端 vs 远端），全 ticker 一键快照。
- `research technical` → `reports/technical_screen_*.md`：美股+加密同一镜头纯价格面
  （动量/趋势/区间位置/ATR/量比/动能判定），不依赖财报事件。
- CLI：`download crypto [--force] [--spots] [--perps]`、`import-crypto`、`import-options`。

**多资产数据坑（新踩）**
- bitget 永续 1d 接口固定只回最新 ~90 根且忽略跨页错位 `since`；**永续日线走 4h 分页重采样**。
- ccxt 分页必须「先取最新一页，再按 `oldest - len(raw)*bar_ms` 回溯」；步长用实际返回根数×周期，
  否则窗口错位返回空 → 数据停留在 1 年前但看似正常（本层第一版就踩了，全量重拉）。
- bitget `fetchOpenInterestHistory` 不支持 → OI 用 `fetch_open_interest` 每日快照追加累计。
- Schwab 链的 `volatility` 字段是 **strike 级 C/P 对称**（同 strike 期权 IV 完全相同）
  → put/call IV 比恒=1.0 无信息；偏斜只能从 call 侧量（25Δ call - ATM）。
- Schwab 前端 0dte 周到期 IV 噪声大 → 前端到期选 DTE≥5 档。
- DuckDB `right` 是保留字，列名必须加引号 `"right"`。
- 永续-现货价格一致性是 crypto 数据的强校验：两者应几乎相等（基差<1%），若差 >5% 视为取数 bug。

**现状结论（观察性，非事件证据）**
- 2026-08-21 快照：NVDA IV 53.8%（近端更高，财报前 IV）、SPY 11%（远端更高=正常 contango）。
- 加密处于空头后反抽：BTC 360d -32.7%、距 ATH -40%，7d +18.7%，资金费率仍微正。
- 期权研究下一步（T 系列）：财报前 IV 研判叠加事件研究、备兑候选数量化（IV+skew+财报日历）。

---

## F. 每日自动化 + 邮件推送（2026-08-21）

**每日任务**（systemd user timer，无需手工跑）
- `~/.config/systemd/user/research-daily.timer`：每天 **23:30 本地(CST)** = 美东 11:30 盘中（期权链最新鲜）；
  `Persistent=true` 错过自动补跑；`RandomizedDelaySec=300`。
- `research-daily.service` 内容 = `python -m apps.research daily --email`，日志 `journalctl --user -u research-daily`。
- daily 编排（`apps/research/daily.py`）单步容错：crypto 下载+入库 → crypto 报告 → 期权链+报告+入库 →
  technical 报告 → 邮件推送；任何一步失败只记录不中断，汇总 5 步状态。

**邮件推送**（`apps/research/email_reports.py`）
- 凭证在 `.keys/.secrets.toml` 的 `[SMTP]`：HOST/PORT/USERNAME/PASSWORD/FROM/TO（TO 逗号分隔多收件人）。
- Gmail 需**应用专用密码**（两步验证时去 myaccount.google.com/apppasswords 生成），不是登录密码。
- 命令：`research email config`（看状态，不打印密码）、`research email test`（发测试信）。
- 未配置时 daily 照跑、仅汇总里提示"未配置（不影响报告生成）"——邮件失败永不影响研究报告。

**每日运行时长参考**：约 25-35s（crypto 缓存命中后极快；期权链 10 ticker 约 20s）。

**Dashboard Research 页**（UI 已从「Options」改名为「Research」，/options 302 → /research）：
- 研究日报 Tab：三份报告直接渲染（markdown→表格），「刷新数据」触发后台线程跑 daily（分析only，不发邮件不下单）。
- 期权备兑分析 Tab：保留原 live chain 分析器（只分析不下单）。
- 后端新路由 `apps/dashboard/backend/routers/research_reports.py`：GET /api/research/reports + POST /api/research/refresh。
- 改前端需 `cd apps/dashboard/frontend && npm run build` 后重启 backend（静态 dist 由其托管）。

**邮件坑**：Gmail 应用密码从网页粘贴常带分组空格（含 `\xa0` 不间断空格）→ smtplib
`encode('ascii')` 直接报 `UnicodeEncodeError`。已修复：载入时 `''.join(非空白字符)` 清洗
（应用密码 =16 位无空格小写字母）。回归测试 `test/research/test_email_reports.py` 覆盖。

---

*最后更新：2026-08-21。新增条目请按文首模板填写。*


## UI / 文档中英双语

- Dashboard UI：右上角「中文 | EN」切换（记忆在 localStorage `qf_lang`）；字典在
  `apps/dashboard/frontend/src/i18n/{zh,en}/pages/*.ts`（每页一个文件，键 `ns.xxx`），
  页面代码用 `useLang().t('ns.xxx')`。改文案 = 改字典即可（会 tsx 页面引用的键要两套一起补）。
- README 采用 deepseek-harness 的双语约定：`README.md`(EN) + `README.zh.md`(ZH) + `README.i18n.yaml`
  （两侧 git blob hash 一致性记录），校验/记录命令 `python scripts/verify-docs-i18n.py [--write] README.md`。
- CLAUDE.md 统一中文。

## 品牌视觉

- 像素风 logo：`assets/quantforge-logo.svg` + `.png`（纯字母版：上下两排 7×7 立方体大写 QUANT/FORGE，2px 笔画、扁平、透明背景、无边框无底色，金字；另有白字版 `quantforge-logo-white.png`）。
- 重新生成：`.venv/bin/python scripts/make_logo.py`（改 `scripts/make_logo.py` 里的像素原语）。README 中英两版都引用同一 PNG。

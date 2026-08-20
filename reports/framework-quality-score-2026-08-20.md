# QuantForge 框架质量评分报告（2026-08-20 · 终版）

评审方式：4 个独立子代理分头审计（execution/brokers、backtest/strategy、dashboard/CLI、domain/options/risk）+ 本人逐条复核关键结论，叠加此前两轮评审与全部修复。

基线：head `75368b8`，工作树含一处本次顺手修复（`DailyEntryCounter._persist` 0600 窗口），194 测试、ruff/tsc/build 全绿。

## 一、总评分：82 / 100（B+ → A-，良好偏优）

| 维度 | 权重 | 得分 | 一句话依据 |
|---|---|---|---|
| 架构与不变量 | 20% | 92 | 单一下单边界、风险闸门不可绕、registry-only 策略、原子多腿期权 |
| 安全与风控 | 20% | 86 | 密钥纪律/0600 全统一、恒时比较、失败即关闭；残留 account_hash 暴露等中低项 |
| 正确性与回测↔实盘一致性 | 20% | 78 | 语义对齐完成；成交时点/杠杆/负现金仍有差异 |
| 测试与质量门禁 | 15% | 76 | 194 测试、42% 测试/源码比；但无 CI、无覆盖率工具、取消路径无测试 |
| 可运维性与健壮性 | 10% | 70 | 长任务取消失效、显式日期区间无上限（资源耗尽向量） |
| 代码卫生 | 5% | 86 | 时间表单一来源、死代码清零；残余潜在面（schwab 直连下单） |
| 前端 | 5% | 82 | strict tsc + noUnusedLocals、构建绿、Options 独立成页 |
| 文档 | 5% | 80 | README/CLAUDE/评审报告齐备，docs/ 覆盖不均 |

加权：18.4+17.2+15.6+11.4+7.0+4.3+4.1+4.0 = **82.0**

## 二、强项（已验证）

1. **不可绕过的风控**：两个真钱入口（`/live/start`、`/options/schwab/run-once`）所有限额都在 pydantic schema 上加 `le=` 硬顶，客户端无法抬高；`require_fresh_quote`/`live_enabled` 仅在这两条路径强制开启；`ExecutionService.execute` 在任何适配器调用前先过 `risk.authorize`，并按 `intent_id` 去重。
2. **未知结果不重试**：`SubmissionOutcomeUnknown`（网络/超时/歧义）只上抛、不重试、不释放，第二条网络提交被挡 —— 防双成交护栏成立（execution/service.py + ccxt）。
3. **卖空/平仓方向**：实盘反转先 reduce-only 平仓再反向开仓，与回测 next-open 语义对齐；平仓被拒不会叠单。
4. **密钥纪律统一**：OAuth 令牌 tmp+`os.replace`+0600、目录 0700；`_save_state`、`OptionReportStore`、`DailyEntryCounter`（**本轮顺手修复**：改为创建即 0600，消除 0644 窗口）三处一致；`secrets.compare_digest`、WS 握手 4401、`sanitize_exception` 不泄漏任何内部细节。
5. **进程级安全**：`flock` 单实例防止跨进程重复恢复/双提交；注册表持久化原子写 0600；`/live/start` 重复引擎闸门已加锁（原子 check-and-insert → 409）。
6. **诚实定价**：所有建模期权报价带 `approximate_unvalidated`；对冲覆盖在 manager 与 risk 两层同时校验；无任何"delta=概率"表述。
7. **无源码执行面**：策略仅 registry 字典查找，无 eval/exec/shell=True，`/source` 显式 404，配置 `extra="forbid"`。

## 三、发现清单（全部经本人复核）

### Medium
- **M1 成交时点不一致**：实盘在决策 bar 的 `close` 成交（live/engine.py:160），回测在 next-bar `open` 成交（backtest/engine.py:126-129）。语义已对齐，但 P&L 无法逐笔映射。→ 建议文档化 + 增补"同一 bar 收盘成交"的确定性回测选项或明确标注。
- **M2 回测引擎保真度**：不建模杠杆（按 allocation≤1 全现金）、`allocation_pct=1.0` 且佣金>0 时现金可为负、无部分成交、止损/止盈出场会被二次计滑点（`close_trade` 再乘 `1±slippage`）。→ 建议：负现金钳制 + 出场不重复计滑点（低风险小改）。
- **M3 任务取消失效**：`run_backtest_job` 整个计算塞进一个 `to_thread`，`check_cancelled` 在线程返回后才执行；optimize 的 `wfo`/`full` 模式根本没传 `job_id`，完全不可中断（grid 模式可以）。（jobs/backtest.py:55、jobs/optimize.py:305,309）
- **M4 显式日期区间无上限**：`_validate_date_range` 只校验 start<end，不限制跨度；客户端可请求 1970→now 的 1m 数据触发海量分页（jobs/data.py + models.py）。与 M3 叠加 = 不可中断的资源耗尽向量。→ 建议服务端加最大跨度（如 ≤5y）并给 bar 数设上限。
- **M5 ledger 结算一致性**：`apply_fill` 按全名义额扣现金、不验证现金充裕度、忽略 `leverage`；`CryptoFuture` 若 `contract_size != 1`，fill 现金用 `multiplier` 而结算 P&L 用 `contract_size`（ledger.py:47-49 vs crypto/lifecycle.py:27-34）。→ 建议统一口径并加现金守卫。
- **M6 `/accounts` 泄漏原始 `account_hash`**：`[account.__dict__ for ...]` 直接把 Schwab 授权用的静态凭据返回给任何持有 API key 的调用方，而已有 `display_id` 掩码字段可用（routers/brokers.py:116）。→ 建议只返回掩码字段。
- **M7 Paper 执行不产生成交**：`PaperExecutionAdapter.submit` 只追加 intent 返回 opaque id，不落账本、不建模成交 —— demo 端到端位置/P&L 遥测不可验证（与"不伪造遥测"决策一致，但 demo 价值打折）。→ 建议后续给 paper 加确定性成交模拟（可选）。

### Low
- **L1** `brokers/schwab.py:814 submit_market_order` 是未调用的直连下单潜在面（生产只走 adapter/ExecutionService）→ 建议删除或注释"仅内部使用"。
- **L2** `_OCC_RE` 要求根与到期日之间有 `\s+`；连续 OCC 符号（`SPY250116C00500000`）永不匹配，静默落入显式字段分支（依赖 Schwab 返回 expirationDate/strikePrice/putCall）。
- **L3** reconciliation 对非 EQUITY/OPTION 持仓（crypto/forex）静默 `continue`，账本低估敞口且不告警。
- **L4** risk `_validate_plan_options` 不聚合账本中已存在的裸空期权（`position.quantity <= 0` 跳过）→ 裸空面被低估（防御性缺口，当前 covered-call 流程不可达）。
- **L5** domain/risk 数值守卫用 `<=0`/`>max`，NaN 可穿透（HTTP 边界已挡，内部构造未挡）。
- **L6** `RiskLimits.require_fresh_quote` 默认 False（默认偏宽松），仅两条实盘路径强制开启；未来新接入路径若直接 `RiskLimits()` 会静默关闭新鲜度。
- **L7** run-once `next(... if symbol == contract_symbol)` 在链中无该符号时报 `StopIteration`→500（当前同一 fetch 内不可达，纯健壮性）。
- **L8** CLI `_http.ServerUnreachable` 只捕获 `ConnectionError`，Timeout/SSL/重置裸抛 traceback；CLI 也未对 422 做友好提示。
- **L9** `web_cmd` 用 `uvicorn --reload`，`web stop` 只 SIGTERM 父进程，reloader 子进程可能残留占用端口。
- **L10** `/auth/start` 的 `_pending_states` 无过期/驱逐，可被刷内存。
- **L11** `~/.quantforge/schwab/` 若已存在，`mkdir(mode=0o700)` 不会收紧既有目录权限。
- **L12** options 仅存在单腿 intent 构造（`intent_from_option_decision` 返回 `OrderIntent`）；无 MultiLeg 反转/滚动构造器 —— 未来需求缺口，非当前 bug。

### 已修复（本轮）
- `DailyEntryCounter._persist` 由 `write_text`（0644 窗口）改为创建即 0600（fd 0o600 + os.replace），与其余持久化纪律一致。

## 四、风险敞口（无法在本环境验证）
- 真实 Schwab OCC 符号是连续还是带空格（决定 L2 走哪条分支）、真实余额字段名。
- OAuth 刷新失败路径日志是否可能带部分 token（未见传播点，未实网验证）。
- 并发 backtest/optimize 无准入控制，`to_thread` 默认池可能被耗尽的资源面。

## 五、下一步优先级
- **P1（低成本高价值）**：M3 取消线程化 + M4 跨度/bar 数上限；M6 account_hash 掩码；M2 负现金钳制与出场滑点。
- **P2**：M1 成交时点差异文档化；M5 ledger/结算口径统一。
- **P3**：L1 清理潜在直连下单面；L11 目录权限校验。
- **工程门禁**：补一个真正的 CI（pytest + ruff + tsc/build），把 194 测试变成护栏而不是"本地常绿"。

## 六、P1 修复记录（补丁批次 · 未再重新评分）

本报告评分对应快照提交 `ef8f0fe`。以下为随后执行的 P1 批次，**逐项复核通过**；全新评分需另起新报告，此处只记录修复事实，不虚增分数。

### 修复项
- **M2 回测引擎保真度**（quantforge/backtest/engine.py）
  - 入场不再让现金为负：`allocation_pct=1.0` + 佣金>0 时按 `min(allocation, 1/(1+commission))` 收紧实际投入比例，成交金额+"入场费"永远装得进现金；无钱可投则跳过该笔交易（不再"借钱"开仓）。
  - 止损/移动止损出场填充价即止损位，不再二次乘 `1±slippage`（`close_trade(..., apply_slippage=False)`）；显式调仓/强制平仓仍按市价计滑点。
- **M3 任务取消失效**（jobs/backtest.py、jobs/optimize.py、jobs/data.py、ccxt.py）
  - `_run_python_backtest(req, job_id)` 全程携带取消钩子：数据分页每页检查（fetch_klines）、回测引擎每 `cancel_check_every`（默认 128）根 bar 检查（`BacktestConfig.cancel_check`）、指标/回撤大循环每 256 点检查。
  - optimize `wfo`/`full` 模式补齐 `job_id` 透传（此前完全没有），每窗口/每阶段检查；`_load_data` 也接受 `job_id`。
  - 取消从"线程返回后才生效"变为"运行中限时生效"，与 M4 叠加不再构成不可中断的资源耗尽向量。
- **M4 显式日期区间无上限**（models.py、jobs/data.py）
  - 请求模型层：显式跨度 >10 年 → 422（`_validate_date_range`）。
  - 任务层：`check_bar_budget` 按 timeframe 折算 bar 数，> `MAX_BACKTEST_BARS`（2,000,000）→ 明确报错（10 年 1d 合法、10 年 1m 拒绝），在任何抓取前执行。
- **M6 `/accounts` 泄漏原始 `account_hash`**（routers/brokers.py + 前端）
  - `/accounts` 只返回 `{account_ref, account_type, display_id}`；`account_ref` 为原始 hash 的 SHA-256 前缀（单向、不可逆推回凭据）。
  - `POST /account` 改按 `account_ref` 选中，服务端内部解析回真实 hash 落盘；真实 hash 永不出服务端。前端 client.ts + SchwabConnection 同步改 `account_ref`。
- **工程门禁**：新增 `.github/workflows/ci.yml` —— uv 同步（含 dev 组）、ruff 检查后端+测试、`pytest -q -m "not slow"`、前端 `npm ci + tsc --noEmit + npm run build`；`main.yml` 的 Notion 同步流程保留不动。

### 测试护栏（新增 12 个，总 194 → 206）
- `test/backtest/test_backtest_engine.py`（3）：满仓+佣金现金永不为负；止损出场精确等于止损价（无双滑点）；`cancel_check` 及时中止。
- `test/dashboard/test_job_cancel.py`（4）：回测任务取消检查在计算内部触发（回归：此前只在 to_thread 返回后）；端到端 `run_backtest_job` → registry `cancelled`；wfo/full 两模式均收到并响应 `job_id` 取消。
- `test/dashboard/test_bar_budget.py`（4）：1m 全局跨度被拒；合理跨度通过；模型层 >10 年 422；5 年 1d 仍可用。
- `test/dashboard/test_broker_accounts_masked.py`（1）：/accounts 无 `account_hash`、ref 不可逆推；按 ref 选中并落盘真实 hash；坏 ref 400。

### 复核状态
- `uv run ruff check quantforge apps/dashboard/backend test` ✅
- `uv run pytest -q` → **206 passed** ✅
- 前端 `npx tsc --noEmit` + `npm run build` ✅（本批次改动涉及前端后又重建）
- 未改动的既定决策：M1 fill 时点差异维持"文档化"语义（不改变行为）；M5 ledger 结算口径仍为 P2；全部 Low 项维持原判。

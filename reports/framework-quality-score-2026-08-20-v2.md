# QuantForge 框架质量评分报告（2026-08-20 · v2 全量重评）

评审方式：与前版一致 —— 4 个独立子代理分头审计（backtest/strategy、dashboard/jobs/CLI、domain/risk/live、execution/brokers）+ 本人逐条复核并亲自实锤验证关键结论（exploit 复现 + 修复），叠加此前三轮评审与全部修复批次。execution/brokers 子代理两次因环境问题未产出，由本人亲自定向复核该区域（下单边界/未知结果/位置查询失败/密钥纪律/多腿原子性）。

基线：head `50dd0b1`，工作树含本轮审查期修复（未提交）；**241 测试**、ruff/tsc 全绿（前端无改动，build 未重跑）。

## 一、总评分：87 / 100（A-，良好偏优 → 优）

| 维度 | 权重 | 旧分 | 新分 | 一句话依据 |
|---|---|---|---|---|
| 架构与不变量 | 20% | 92 | 92 | 单一下单边界再次确认无绕过；裸空聚合漏洞（跨 strike 冲抵）本轮补严 |
| 安全与风控 | 20% | 86 | 90 | 实锤修复 NaN strike 裸空 put 逃逸（High）与 NaN 杠杆绕过现金守卫；构造层+risk 双层 fail-closed |
| 正确性与回测↔实盘一致性 | 20% | 78 | 87 | M2 满仓/止损、M3 取消、M5 结算口径统一全部落地；本轮补 NaN 结算价守卫 |
| 测试与质量门禁 | 15% | 76 | 85 | 194→241 测试（+24%），测试/源码比 42%→47.7%；CI 真实化（空 slow 过滤器已修）；仍无覆盖率工具 |
| 可运维性与健壮性 | 10% | 70 | 82 | 取消全路径（含 options 分支）、日期/bar 预算、CLI 错误面、进程组停止、pending 驱逐全部落地；新增发现：并发任务无准入 |
| 代码卫生 | 5% | 86 | 88 | NaN 数值守卫一致化（intents/risk/ledger/settle/构造层）；无死代码、无 TODO |
| 前端 | 5% | 82 | 82 | strict tsc + noUnusedLocals、构建绿、取消按钮齐备；无改动 |
| 文档 | 5% | 80 | 80 | M1 docstring、CLAUDE/README 齐备；docs/ 覆盖仍不均 |

加权：18.4 + 18.0 + 17.4 + 12.75 + 8.2 + 4.4 + 4.1 + 4.0 = **87.25 → 87**

## 二、本轮审查发现与修复（全部实锤复核）

### High —— NaN strike 裸空 put 资金逃逸（已修复）
- **路径实锤**：`EquityOption.__post_init__` 只查 `strike <= 0`（NaN 通过）；risk 的 `required_cash = uncovered * strike * mult` 中 `NaN > cash` 恒 False → **$1 现金即可授权一手裸空 NaN-strike put**（本人脚本复现：`AUTHORIZED`）。
- **第二层隐蔽性**：`put_short` 合并用 `max(strike, inst.strike)`，Python 的 `max(0.0, NaN)` 坍缩为 **0.0**（NaN 比较恒 False），NaN 实际以 0-strike 进入金额计算，`0 > cash` 也恒 False —— 即使构造层漏网，risk 层也会放行。
- **修复**（双层 fail-closed）：
  1. `instruments.py`：`EquityOption.strike/multiplier` 与 `CryptoDerivative.contract_size/max_leverage` 全部改为 `isfinite + 正数` 校验；
  2. `risk/engine.py` `_validate_plan_options`：required_cash 前显式拒绝非有限或非正 strike/multiplier。
- 回归测试：NaN strike 构造拒绝 + risk 层拒绝（绕过 `__new__` 构造）+ NaN max_leverage 无法绕过现金守卫。

### Medium —— closing 平仓腿跨 strike 冲抵（已修复）
- `closing` 字典按 `(underlying, expiration)` 聚合，**不含 strike**：一个孤儿 BUY-reduce 腿（@90）可抹掉同到期不同 strike（@100）的裸空面。
- **修复**：closing 键纳入 strike（`(underlying, expiration, strike)`），仅同一 strike 的平仓腿可冲抵；分桶键保持 2 元组不变。回归测试：孤儿平仓腿不再掩盖裸空。

### Medium —— options 回测不可逐 bar 取消（已修复）
- `run_managed_covered_call_approximation` / `run_covered_call_approximation` 无 cancel 钩子：2M bar 的 options 回测取消滞后到循环结束（结果安全但不即时）。
- **修复**：两个函数新增 `cancel: Callable[[], None] | None`，主循环每 256 根检查一次；`jobs/backtest.py` options 分支透传 `cancel=cancel`。与共享引擎的 `cancel_check_every=128` 同构。

### Medium —— CI `-m "not slow"` 空过滤器（已修复）
- 全仓无 `@pytest.mark.slow` 测试，`-m "not slow"` 是空过滤器（原本想跳过的慢/联网测试意图未实现）。
- **修复**：CI 改为 `uv run pytest -q` 全量（全套 3.3s，无需慢标记）。

### Low —— 全部修复
- `risk_options.py`：Schwab 连接/快照异常包装为 503/502 `safe_exception_detail`（此前裸异常→通用 500，错误面与 brokers.py 不一致）；run-once 的 409（链分歧）提前到报告持久化**之前**，不再留孤儿报告。
- `settle_crypto_future`：结算价 NaN 拒绝（此前 `NaN <= 0` 通过 → NaN P&L 脏账，后续算术无法察觉）。
- `ledger.apply_fill`：`max_leverage` 非有限/非正一律回退 1（现金守卫方向），不再 `or 1` 保留 NaN。

## 三、确认项（本轮独立复核成立，无回归）

1. 下单边界：全部订单经 `ExecutionService.execute`（intent_id 去重 → `risk.authorize` → adapter.submit）；框架内除 adapters 外无下单调用点（live/engine 仅经 execution 包装）。
2. `SubmissionOutcomeUnknown` 只上抛、不重试、不释放；重复 intent_id 直接返回既有回执 —— 防双成交护栏成立。
3. `CcxtPositionError`（查询失败）≠ 仓位为空，失败即关闭。
4. 密钥纪律：令牌 0600/目录 0700（含既有目录收紧 L11）、tmp+os.replace、`secrets.compare_digest`、WS 4401；账户仅暴露 `account_ref` 掩码。
5. 回测/实盘共享策略实现 + next-bar-open 语义（文档化，options 回测同构）；满仓+佣金无负现金（`effective_ratio` 边界全部正确）；止损不二次计滑点。
6. 取消/预算：数据分页、引擎每 128、指标每 256、options 每 256（本轮）；10 年跨度 422 + 2M bar 任务层双保险，均在任何抓取前。
7. 认证无绕过：`/api*` 中间件 + 精确 `/api/health` 豁免 + WS 自检；非回环绑定无 key 被 CLI/start.sh 拒绝。
8. 裸空覆盖：计划腿 + 账本空头 + 同 strike 平仓冲抵（本轮）+ 股票/长腿对冲，covered-call 流程不被误拒。
9. 每日限额：post-increment 原子 0600 持久化、未命中回滚、run-once 与 live 共享同一预算。

## 四、残余项（已知，不构成本轮扣分主因）

- **Medium-Low（新增发现）**：backtest/optimize 任务并发**无准入控制**（无 Semaphore/并发上限）——M4 已限制单任务资源，但 N 个合法并发仍可耗尽 `asyncio.to_thread` 池。建议加并发上限 + 排队。
- **Low（已知设计）**：`live/engine` 在 close 提交后乐观置 flat（`_target=0`），open 被拒时留 flat 待下 bar 重估——注释明确、broker 快照对账兜底，属 submission-then-reconcile 设计而非缺陷；改为 fill 确认式是更大的设计变更。
- **Low（已知限制）**：`put_short` 跨 strike 合并取 `max(strike)` 是安全上界（不过度放行），但可能过度保守误拒多 strike 短 put 组合；covered-call 策略只持短 call，实际不触发。
- **Low**：L6 告警（live 引擎未开 `require_fresh_quote`）在 demo/paper 与测试构造时高频触发，可能稀释信号——维持 warning 不升级硬错误（demo 合法用 bar-close），可考虑按进程降频。
- **Low**：`killpg` 为 POSIX 专有（Windows 不可用）；目标部署平台为 Linux，可接受。
- **L12（backlog）**：MultiLeg 反转/滚动构造器是功能缺口非缺陷，移入 backlog。
- **无法沙箱验证**：真实 Schwab 符号/余额字段名、OAuth 刷新、实盘并发准入仍需实网确认。

## 五、验证基线

- `uv run ruff check quantforge apps/dashboard/backend test` ✅
- `uv run pytest -q` → **241 passed**（194 → 206 → 235 → 241）
- `npx tsc --noEmit` ✅（前端无改动，build 未重跑；CI 将覆盖）
- 本轮修复均在审查期内完成并附回归测试；修复未提交（见 git status），提交由操作者决定。

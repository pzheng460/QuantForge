# QuantForge 框架代码复审报告（2026-08-20 跟进）

复审范围：commit `4aa97aa`（4 个优先问题修复）的正确性与遗留问题。
基线：`a6d42b5`（Python-first 迁移）。当前状态：198 个测试通过，ruff/tsc/前端构建全绿，工作树干净。

## 一、4 个优先问题的修复复核结果

### 1. 实时绩效死代码移除 — 通过 ✅
- 端到端一致：后端（`/live/performance`、WS、模型）、CLI（`engines performance`）、前端（WS 客户端/store/`liveAdapter.ts`/`LivePerformance` 类型）全部清除，无悬挂引用（grep 全库验证）。
- dashboard 右侧面板改为展示 `/live/engines` 注册表的真实状态（含失败原因、起停时间），符合"不伪造遥测"原则。
- `websocket_api_key_authorized` 仍被 backtest/optimize WS 使用，保留正确。
- 前端开启 `noUnusedLocals/noUnusedParameters`，tsc 通过即证明无残留死代码。
- 复核建议（与本次修复无关）：`models.py`/`main.py` 无残留；`test_ws_auth.py` 删 3 个用例后结构自洽。

### 2. 回测/实盘反转语义统一 — 通过 ✅
- `process_bar` 状态机复核：`reversal`/`closing_to_flat`/`open` 三分支覆盖全部 (target, 当前) 组合，无缺口。
- 平仓指令方向取自**变更前** `self._target`，先建后改，正确。
- 平仓被风险闸门拒绝 → 反转整体中止（不叠单）；开仓被拒 → 状态置平、下一根 bar 重估。两条失败路径的行为都有测试锁定。
- 已知取舍（非缺陷）：反转产生两根同价 MARKET 单（先减后开），与回测的"next open 先平后开"在语义上对齐；`process_bar` 返回值只保留最后一单回执，平仓回执不对外暴露——当前无调用方依赖。

### 3. OHLCV 未收盘 bar 口径统一 — 通过 ✅
- dashboard pager（`jobs/data.py`）与 canonical `fetch_klines` 采用同一规则：只丢弃当前正在形成的半成品 K 线。
- 顺带修复了 canonical 侧的真实 off-by-one：`((now//bar)-1)*bar` 会连**刚收盘的 bar** 一起丢掉，实盘信号滞后一整根。边界改为 `(now//bar)*bar`，正好只丢进行中那根。
- 两处边界行为均有测试锁定（含"历史窗口不受影响"回归）。
- 次要：`fetch_klines` 分页循环上界仍是 `end_ms` 而非 `effective_end_ms`（多取一页再过滤），仅性能；文档字符串仍引用已删除的 `cli._fetch_ohlcv`。

### 4. 真钱路径测试覆盖 — 通过 ✅（且抓到真 bug）
- 新增 53 个测试（145→198）：CCXT 持仓查询 fail-closed（含 UTA 路径）、`_build_runtime` 启动对账（失败拒启/长空短空方向）、`SchwabExecutionAdapter` 指令映射（BUY_TO_COVER/SELL/SELL_SHORT/_TO_OPEN/_TO_CLOSE、原子多腿、歧义→SubmissionOutcomeUnknown 不释放）、风险闸门（裸期权、报价新鲜度/价差、杠杆/名义额/腿数、日开仓回滚）、持久化 `DailyEntryCounter`。
- **新测试抓到并修复了一个真实安全 bug**：本地路径的日开仓限额检查用自增前值，允许 `max_daily_new_positions + 1` 次开仓；`risk/engine.py` 已与共享 `DailyEntryCounter` 统一为自增后语义并有测试锁定。

## 二、仍存问题（按优先级）

### P1（无）— 未发现新的高危问题
4 个优先项修复后未引入新的安全/正确性回归。

### P2（建议短期内处理）
1. **遗留 portfolio 回测器为死代码**：`quantforge/portfolio/{simple_trend,strategy_backtest,trend_pullback}.py`（约 965 行）只被各自测试引用，产品路径全部走 `quantforge/backtest/` 共享引擎。建议删除（连同 `test/portfolio/` 三个测试文件）或在包内显式标记废弃，避免与 canonical 引擎混淆。

2. **时间框架表与 OHLCV 分页器仍然重复**：`_TF_SECONDS` 在 `ccxt.py` 与 `jobs/data.py`（外加 `_TF_MS`）各一份；`fetch_klines` 与 `_fetch_crypto_ohlcv` 是结构相同的 ccxt 分页循环——这正是第 3 项问题产生漂移的同类风险。建议收敛：`jobs.data._fetch_crypto_ohlcv` 直接委托 `fetch_klines`，时间框架表统一到单一模块。

### P3（低成本加固）
3. **`OptionReportStore.save` 用未经校验的 ticker 拼路径**：`root / report.ticker / f"{stamp}.json"`，ticker 直接来自 `request.ticker.upper()`。当前实际不可达（无效 ticker 在链数据抓取阶段就失败），但属于纵深防御缺口——在请求模型上校验 `^[A-Z]{1,5}$`。

4. **`_save_state` 未设 0600**：与 `DailyEntryCounter`/`OptionReportStore` 的"原子写 + 0600"纪律不一致（内容不含密钥，仅配置）。顺手对齐。

5. **`/live/start` 重复引擎保护是 check-then-act**：`list_engines()` 后 `start_engine()` 非原子，两个并发请求可能各启一个同名引擎。低风险，可加进程内锁。

### P4（风格/UX）
6. **`OptionsAnalysisPanel` 嵌入 Live Trading 页**（`a6d42b5` 引入，分析-only 不自动下单）：在实盘页面混入期权推荐面板，建议移到独立页面，让"实盘交易"页聚焦引擎控制。
7. **CLI `engines start` 不透传 risk limits**，走服务端默认值；行为与 API 不一致，建议在帮助文本注明。

## 三、结论

4 个优先问题修复正确、互相独立、无回归；测试增量（+53）不仅锁定了关键不变量，还暴露并修复了一个真实的日限额 off-by-one 缺陷。框架核心（Python-first Strategy→Risk→Execution 单一下单边界、fail-closed 语义）目前是健康的。剩余问题集中在**重复代码收敛**（P2 #1/#2）与**小范围加固**（P3），无阻断项。

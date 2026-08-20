# QuantForge 框架代码质量审查报告

日期：2026-08-20
审查范围：`quantforge/`（核心框架）、`apps/dashboard/backend/`（Web 后端）、`quantforge/cli/`（CLI）、
`apps/dashboard/frontend/src/`（参数化 UI）、`test/`（测试）。
审查提交基线：`HEAD = 595941c`（工作树含未提交的大规模迁移变更，见 §6）。

---

## 1. 基线检查结果

| 检查 | 结果 |
|---|---|
| `ruff check quantforge apps/dashboard/backend test` | ✅ 全部通过 |
| `uv run pytest -q` | ✅ **148 passed**（约 1.3s，全部绿灯） |
| 覆盖率（pytest-cov 实测） | ⚠️ **总计 67%**，且真实资金路径严重偏低（见 §3.4） |
| 前端 `npx tsc --noEmit` | ✅ 通过 |
| 静态类型（mypy/pyright） | ❌ **未配置、未运行** |
| 前端 lint（eslint） | ⚠️ 有 `npm run lint` 脚本，但没有作为 CI/提交门槛 |

---

## 2. 总体评价

这是一个架构清晰、风险意识强的量化框架。分层（`strategy → domain → risk → execution → brokers/adapters`）
与 CLAUDE.md 描述的规范路径一致；"策略只发 intent、永不直连 broker"、"风控不可绕过"这两条核心纪律在
代码里是**真实实现**而不仅是文档承诺。质量明显高于一般个人项目：错误消息卫生、原子写文件、权限位收紧、
单实例锁、fail-closed 语义都有系统性的处理。

主要问题集中在三类：**① 迁移残留的死代码与双实现漂移；② 真实资金路径的测试覆盖过低；
③ 回测与实盘之间的语义不一致**。以下按严重度列出具体发现。

---

## 3. 高优先级问题（建议先处理）

### 3.1 实盘性能监控已变成死代码：没有任何写入方

`apps/dashboard/backend/routers/live.py` 的 `/live/strategies`、`/live/performance`、`/ws/live/performance`
以及 CLI 的 `quantforge-cli engines performance` 全部读取
`~/.quantforge/live/<strategy>/live_performance.json`，但**整个代码库中已不存在任何写该文件的逻辑**
（删除 `quantforge/evolving/` 时把原 `paper_ledger`/性能写入器一并删掉了，仅剩读者）。

后果：
- `/live/performance` 永远返回空 `LivePerformanceOut`，WS 推送永远不会发数据，前端"实时性能"面板空转；
- `engines_cmd.py` 读取的键名（`return_pct`/`win_rate`/`max_drawdown`/`last_bar_at`）与
  `LivePerformanceOut` 模型（`total_return_pct`/`win_rate_pct`/`max_drawdown_pct`/`last_update`）**不一致**，
  即使未来有人补上写入器也会立即显示错误数据（`ret * 100` 对已是百分数的字段会再乘 100）。

建议：要么由 `PythonLiveEngine` 事件循环补齐性能持久化（与回测指标同口径），要么删除这些死端点与死 CLI
命令，避免维护者误以为实时监控是工作的。

### 3.2 回测与实盘对"直接反转"的语义不一致

- 实盘引擎 `quantforge/live/engine.py`：`if self._target and target.position not in {0, self._target}: raise RuntimeError("reversal requires an explicit flat target first")` —— 要求先平仓再反向。
- 共享回测引擎 `quantforge/backtest/engine.py`：`pending = target if target.position != position or ...` —— **允许 1 → -1 一步反转**，下一根 bar 先平后开。

于是一个一步反转的策略在回测里表现正常、上实盘第一天就 crash（并非被风控拦下，而是 `RuntimeError`，
进而触发看门狗重启预算）。这是最容易在"策略由回测搬上实盘"时踩中的坑。建议：回测引擎与实盘引擎
采用同一套反转判定（回测也不允许一步反转，或实盘按两 bar 处理），并加一个针对该契约的测试。

### 3.3 行情数据存在两套抓取实现，对"进行中 bar"处理相反

- `quantforge/adapters/ccxt.py::fetch_klines` 明确丢弃"当前未收盘 bar"（`effective_end_ms = last_closed_start_ms`），
  其 docstring 声称这是为了消除 backtest-vs-live 分歧；
- `apps/dashboard/backend/jobs/data.py::_fetch_crypto_ohlcv` **没有**丢弃未收盘 bar。

默认 `end_date = today` 时，dashboard 回测会把最后一根未收盘的部分 bar 塞进历史数据，而 CLI/live
warmup 不会 —— 同一个周期两种取数结果不同，且部分 bar 的 OHLC 会在收盘后被改写，造成"同一段数据多次
回测结果漂移"。建议：`jobs/data.py` 改为消费 `fetch_klines` 或复用同一套截断逻辑（同时消除 §4.1 的重复）。

### 3.4 真实资金路径测试覆盖过低

总覆盖 67%，但风险最高的路径反而最低：

| 模块 | 覆盖 | 主要缺口 |
|---|---|---|
| `adapters/ccxt.py` | 37% | Bitget UTA 下单、idempotency、position fail-closed、杠杆/保证金设置 |
| `adapters/schwab.py` | 31% | intent→Schwab 指令映射、multi-leg 原子提交 |
| `brokers/schwab.py` | 57% | OAuth exchange/refresh（152–192）、超时→Ambiguous、重试、撤单、下单 |
| `apps/.../live_engines.py` | 35% | `_build_runtime`、看门狗重启、emergency_halt_all、恢复引擎 |
| `jobs/optimize.py` | 15% | grid / wfo / three-stage 全部未测 |
| `jobs/data.py` | 28% | 抓取/分页/重试逻辑 |
| `indicators/streaming.py` | 27% | ATR/ADX/RSI/ROC/BB 的数值正确性 |
| `strategies/technical.py` | 49% | 多数策略只测了入口 |

其中 `SchwabAmbiguousOrderError / SubmissionOutcomeUnknown`（订单结果未知 → 禁重试防双成交）和
`CcxtPositionError`（查询失败不得当作空仓）是**用钱买出来的正确性保证**，但它们几乎没有测试覆盖。
建议优先补齐：`brokers/schwab.py`（用 fake session/HTTP 断言各状态分支）、`adapters/ccxt.py` 的
UTA 与 unknown-outcome 路径、`live_engines.py` 的看门狗与应急暂停（已有 `test_watchdog.py`，可扩展）。

---

## 4. 中优先级问题

### 4.1 重复实现 / 单一事实来源漂移

- `_TF_SECONDS` + `timeframe_to_seconds` 在 `quantforge/adapters/ccxt.py` 与 `apps/dashboard/backend/jobs/data.py`
  各有一份；`_VALID_TIMEFRAMES`（models.py）是第三处白名单，三处需手工保持同步。
- CCXT 抓取分页逻辑：`fetch_klines`（ccxt.py）与 `_fetch_crypto_ohlcv`（jobs/data.py）近乎重复，且已出现
  行为分歧（§3.3）。ccxt.py 的 docstring 自称"centralise"，但 backend 没有消费它。
- 绩效计算：`jobs/backtest.py` 的指标块、`jobs/optimize.py::_metrics`、`portfolio/*._performance` 三套
  近似的 Sharpe/DD/收益实现，口径（付息期、年化）不完全一致。
- 期权近似回测：`run_managed_covered_call_approximation` 与 `run_covered_call_approximation` 内联
  重复的 strike 扫描 + 定价循环。
- `portfolio/simple_trend.py`、`trend_pullback.py`、`strategy_backtest.py` 是同一个"DEPRECATED 遗留程序化
  回测"的三种变体（`_day`/`_performance`/`_validate` 复制粘贴）。

这些文件大多已自标 DEPRECATED 或说明保留原因，可接受；但 **时间框架表和 OHLCV 抓取**是三/五处真正的
运行时事实源，建议合并。`_DEFAULT_SYMBOLS` 也已从 jobs.data 被 live_engines 引用，说明跨层共享是
既有模式，顺理成章。

### 4.2 跨模块访问私有成员

- `live_engines.py:193`：`connector._exchange.market(symbol)` 直接摸 `CcxtConnector` 的私有 `_exchange`；
  且用 `**market, "maxLeverage": ...` 重建字典伪装成 ccxt market 数据喂给 `instrument_from_ccxt_market`。
  建议给 `CcxtConnector` 提供 `get_market()` 公共方法。
- `live_engines.py` 的 `watch()` 读 `engine._warmup_complete`（私有）。建议暴露为属性。
- `strategy/indicators.py` 的 `crossover/crossunder` 直接读 `other._history`（同包内可接受，但换成属性
  或公共方法更稳）。

### 4.3 类型薄弱处

- `StrategyContext.market: Any`、`Strategy.on_event(ctx, event: Any) -> list[Any]` —— 框架最核心的
  策略 API 类型基本未建模（事件驱动策略与 bar 策略解耦后，`on_event` 无签名约束，`OptionsEventEngine`
  只能靠 `runtime` 检查"恰好一个 OptionDecision"兜底）。
- `BrokerConnector` 协议形同虚设：`brokers/protocol.py` 定义了 `place_order` 协议，但 `SchwabConnector`
  与 `CcxtConnector` 都未显式声明实现它，也没有 `isinstance` 检查消费方。
- 项目没有 mypy/pyright 配置。以本代码库的规模（~9000 行）和质量目标，值得加 `mypy --strict`（至少
  `--check-untyped-defs`）或至少启用 ruff 的 `ANN`/`B`/`SIM`/`UP`/`I` 规则集——当前 `.ruff.toml` 只开了
  `E4/E7/E9/F` 最小集，lint 基本形同虚设（这也是 148 个测试之外唯一机械门槛）。

### 4.4 安全相关（多为低危，loopback 默认绑定缓解）

- API key 允许走 `?api_key=` 查询参数（WS 只能如此，但 HTTP 也接受之），会进代理/访问日志；`client.ts`
  对 HTTP 请求也统一附加查询参数。HTTP 侧建议仅支持 header。
- `routers/brokers.py::_pending_states` 只增不减（600s TTL 但无清理线程/惰性 GC），攻击者可刷 `/auth/start`
  累积内存——loopback 默认下可忽略，但绑 0.0.0.0 后是开放面。
- `live_engines._save_state()` 写 `engines.json` 未加 `chmod 0600`（其余持久化文件都设了），虽不含密钥，
  但与其他文件安全姿态不一致。
- dashboard dev 前端 `npx vite --host 0.0.0.0` 绑定全网卡（start.sh:110）；CORS 只放行 5173，浏览器
  跨源被拦，但这把 SPA 本体暴露到了网络且依赖 API-key 补丁，与后端 loopback 的严格姿态不完全一致。

### 4.5 产品文案与逻辑耦合

`options/actions.py` 的常量值（"开 Covered Call" 等）与 `OptionManager.evaluate()` 返回的中文 reasons
（"已获取至少 70% 权利金…"）把展示文案硬化进逻辑层；reason 里硬编码的 "70%" 与 `profit_take` 设置解耦，
改参数后文案失真。建议 actions 保留机器标识（如 `OPEN_COVERED_CALL` 这类英文 key），展示文案交给
dashboard/前端映射，与 actions.py 自己声明的理念一致。

### 4.6 杂项

- `apps/dashboard/backend/main.py:17-20` 留有一段与代码脱节的注释片段（"…gets dropped silently because
  uvicorn only configures its own logger. In live trading this means we have ZERO observability"），
  像是迁移时遗留的提交信息残片，与下方实际代码无关，应删。
- `strategy/indicators.py::_MAX_HISTORY = 256` 与 `__getitem__` 的 `n >= len(...)` 边界是隐式约定，无文档。
- `cli/commands/engines_cmd.py` 的 `start --symbol` 默认 `BTC/USDT:USDT` 对 schwab 不适用（schwab 应是美股
  代码）；且 `--leverage` 型默认值未对齐后端 schema。
- `jobs/backtest.py::_approximate_earnings_calendar` 用固定 91 天近似财报日并硬编码 `+182 天` 水平线，
  已在 docstring 说明；建议把参数化为配置并注明这是"非真实日历"的建模假设。
- `OptimizeRequest.metric` 未做白名单校验，非法值静默回退到 sharpe（jobs/optimize.py:166），应 422。

---

## 5. 优点与值得保持的部分

- **架构纪律落实得扎实**：`ExecutionService` 是唯一下单边界；`RiskEngine` 对裸卖权做**跨 leg 聚合**
  校验、对 reduce_only 做**按 instrument 净值**核对（防 multi-leg 内 BUY-reduce 与 SELL-reduce 净翻转）、
  引用 `quote_timestamp=None` 时不伪造新鲜度；`SubmissionOutcomeUnknown` 语义在 Schwab/CCXT 两层都
  正确实现并贯通（绝不自动重试 → 防双成交）。
- **防止"查询失败=空仓"**：`CcxtPositionError` 与 `get_position` 的 fail-closed 处理，实盘重建时先读
  broker 真实持仓再启动 —— 这是真正的实盘安全设计。
- **错误卫生**：`http_errors.py` 统一 sanitize 掉路径/密钥并只回传异常类名；日志留全量 traceback。
- **持久化安全姿态**：token store、daily-entries、risk-control 全部 `tmp + os.replace` 原子写 + `0600`。
- **回测诚实性**：`approximate_unvalidated` 标记、next-bar-open 填价、`_PendingAction` 决策 bar 与
  成交 bar 分离 —— 建模纪律在代码注释里反复出现且确实如此实现。
- **文档**：README、CLAUDE.md、模块 docstring 与实现大体同步；DEPRECATED 模块全部自标。
- **测试**：148 全绿；`domain`(95%)、`risk/control`(86%)、`options/manager`(90%)、`portfolio/*`(92–96%)、
  `backtest/engine`(82%)、`live/engine`(87%) 这些纯逻辑核心覆盖健康。

---

## 6. 仓库卫生 / 提交状态

- `git status` 显示 **180 个文件、+3243/−14335 行 的未提交迁移**：`quantforge/evolving/`、`eval/`、
  `quantforge/agent_providers.py`、多个 CLI 命令、`CLAUDE_CN.md` 全部处于"已删除但未提交"状态，
  同时大量新文件 untracked。代码内已无任何残留 import/引用（grep 验证过），迁移本身是干净的，但
  **工作树处于"大改未提交"的中间态**，建议尽快拆成几笔有意义的 commit 提交，降低丢失风险。
- 已确认 `.env`、`.keys/` 未被 git 跟踪，密钥未外泄。

---

## 7. 建议处理顺序

1. **提交/收敛当前迁移工作树**（§6）——先固化基线。
2. 修 §3.1 死代码：删端点或补写入方；顺带修正 `engines_cmd` 的键名与默认 symbol。
3. 统一 §3.2 反转语义（回测对齐实盘，加契约测试）。
4. 合并 §3.3/§4.1 的 OHLCV 抓取与时间框架表（`jobs/data.py` 复用 `fetch_klines`），消除 in-progress-bar 分歧。
5. 补 §3.4 高危路径测试（Schwab 状态机、CCXT unknown-outcome、live_engines 看门狗/应急暂停）。
6. 加显式类型检查（mypy 或 ruff 扩展规则集）并把前端 eslint 纳入 CI。
7. 低优先级：文案解耦、安全小项（HTTP 仅 header key、_pending_states GC、engines.json 0600）。

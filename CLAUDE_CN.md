# CLAUDE_CN.md

为在本仓库工作的 Claude Code（claude.ai/code）提供的中文指引。

## 项目概览

QuantForge 是基于 Python 3.11+ 的量化交易平台，由**三个松耦合模块**组成：

1. **Pine 引擎** (`quantforge/pine/`) — TradingView 兼容的 Pine Script v5
   解析器、解释器、转译器、优化器、实盘引擎。**所有交易策略都是 `.pine`
   文件**，这是主要的策略层。
2. **DSL 引擎** (`quantforge/dsl/`) — 一套轻量的声明式 Python API
   (`class MyStrategy(Strategy): on_bar(...)`)，用于快速原型。
3. **Web 面板** (`apps/dashboard/`) — FastAPI 后端 + React/Vite 前端，
   把 Pine/DSL 的能力以 UI 形式包装出来：Live、Backtest、Grid Optimize、
   AI Optimize。

交易所连接**直接走 `ccxt`** — 实盘链路里没有任何手写的单交易所
OMS/WebSocket 代码。Pine 引擎通过 `quantforge/pine/live/connector.py::CcxtConnector`
调用 `ccxt.binance(...)` / `ccxt.okx(...)` 等。

## 开发命令

### 安装
```bash
uv sync                       # 安装运行依赖
uv sync --group dev           # 安装开发/测试依赖
uv add --dev pre-commit && pre-commit install   # 贡献代码前必装
```

### 测试
```bash
uv run pytest                                       # 全套

uv run pytest test/pine/ -v                         # 107 Pine 引擎
uv run pytest test/dsl/ -v                          # 35 DSL
uv run pytest test/cli/ -v                          # 18 CLI 表面
uv run pytest test/dashboard/ -v                    # 71 后端路由
uv run pytest test/optimizer_ab/ -v                 # 26 A/B 框架
```
`pytest.ini` 启用了 `asyncio_mode = auto`。总计约 257 个测试。

### 代码质量
```bash
uvx ruff check                # lint
uvx ruff format               # format
```

## Web 面板 (`apps/dashboard/`)

```
apps/dashboard/
├── backend/
│   ├── main.py              # FastAPI 应用；prod 模式下挂载 frontend/dist/
│   ├── jobs/                # 后台任务包（__init__ 作 facade）
│   │   ├── registry.py      #   内存任务表 + 取消机制
│   │   ├── data.py          #   _fetch_ohlcv / _resolve_pine_source / 日期区间
│   │   ├── backtest.py      #   run_backtest_job / _run_pine_backtest
│   │   └── optimize.py      #   run_optimize_job / _run_wfo / _run_three_stage
│   ├── live_engines.py      # in-memory 引擎管理器（start/stop/list）
│   ├── models.py            # Pydantic 请求/响应模型
│   └── routers/
│       ├── strategies.py    # /api/strategies, /api/exchanges
│       ├── backtest.py      # /api/backtest/{run, {id}, cancel/{id}}
│       ├── optimize.py      # /api/optimize/{run, {id}, cancel/{id}, ws/{id}}
│       ├── live.py          # /api/live/{start, stop/{id}, engines, performance, ws}
│       └── agent.py         # /api/agent/{skills, run, {id}, {id}/stop, ws/{id}}
├── frontend/
│   ├── src/
│   │   ├── App.tsx          # Linear/Vercel 风顶栏 + 导航 + ErrorBoundary
│   │   ├── pages/{Dashboard,Backtest,Optimizer}.tsx
│   │   ├── components/
│   │   │   ├── ResizableSidebarShell.tsx  # SidebarProvider + 可拖拽手柄
│   │   │   ├── ResizeHandle.tsx           # capture-phase pointer 事件
│   │   │   ├── StrategyTester.tsx
│   │   │   ├── AgentTraceViewer.tsx
│   │   │   ├── MetricsSummary.tsx
│   │   │   ├── charts/TradingChart.tsx
│   │   │   └── ui/                        # shadcn 原语（Button、Input 等）
│   │   ├── api/client.ts                  # 基础路径 `/api`，ApiError 类
│   │   ├── hooks/use-queries.ts           # React Query hooks
│   │   ├── stores/{dashboard,backtest,optimizer}Store.ts (zustand)
│   │   └── types.ts
│   └── vite.config.ts                     # dev 模式 /api → :8000 代理
└── start.sh                                # dev (vite HMR + uvicorn --reload) | --prod (vite build + StaticFiles)
```

### 前端设计系统

- **技术栈**：React 18 + Vite 5 + TypeScript（strict）+ Tailwind 3 +
  shadcn/ui（Radix） + lightweight-charts + recharts + zustand + react-router 6。
- **主题**：冷调 Linear/Vercel 风格。CSS 变量定义在 `index.css`：
  - `--background` 纯白、`--surface` zinc-50（卡片背后的微冷调底色）、
    `--card` 纯白。
  - `--primary` zinc-900 (#18181B)、`--brand` blue-500 (#3B82F6)。
  - 盈亏语义色：`--positive` green-600、`--negative` red-600。
  - 精细阴影 `--shadow-{xs,sm,md,lg,glow}`，替代默认的飘软阴影。
- **字体**：**Geist** sans + **Geist Mono**（不用 Fraunces / Inter）。
  正文 letter-spacing `-0.011em`；所有数字走 mono + `tabular-nums`。
- **布局不变式**（不要破坏）：
  - 根 div `h-screen overflow-hidden flex-col` — 视口硬封顶。
  - SidebarProvider 加 `!min-h-0 h-full overflow-hidden` 覆盖 shadcn 默认
    的 `min-h-svh`（否则子项 content 一长就撑穿）。
  - chart pane 一律 `flex-1 min-h-0 overflow-hidden`，让
    lightweight-charts 的 canvas 能在 flex 压力下真的收缩。

### dev 与 prod 模式

```bash
./apps/dashboard/start.sh           # dev：vite :5173 + uvicorn :8000 --reload
./apps/dashboard/start.sh --prod    # prod：vite build → dist/，FastAPI 在 :8000 直接 serve SPA
./apps/dashboard/start.sh stop      # 停止两者
```

dev 模式的 `--reload` **限定范围**到 `apps/dashboard/backend` 和
`quantforge/`，避免 StatReload 浪费一个 CPU 核扫 `eval/`、`node_modules/`、
agent job JSON 等。prod 模式不开 `--reload`，通过 FastAPI `StaticFiles`
挂载 `apps/dashboard/frontend/dist/` + SPA fallback 到 `index.html`。

### 可拖拽侧栏 (`ResizableSidebarShell`)

每个页面（Dashboard / Backtest / Optimizer）都把 `SidebarProvider` 包在
`ResizableSidebarShell` 里，用户可拖拽右沿调宽。宽度按页持久化
（`localStorage["sidebar-width:<storageKey>"]`），双击重置。

页内的垂直分割（chart ↔ 底部面板）用同一个 `ResizeHandle` 组件。**有个
踩过一次的坑，不要再踩**：lightweight-charts 会在自己容器上挂
`pointermove` 监听器，整片图区域都会捕获指针事件。直接用
`window.addEventListener` 的处理器会被它饿死。所以 hook 用**文档级、
capture-phase 的监听器**（`document.addEventListener('pointermove', ..., true)`）
抢在 chart 的 bubble-phase 之前触发。代码在
`apps/dashboard/frontend/src/pages/{Dashboard,Backtest}.tsx` 的
`useResizablePanel()` 里。

### AI Optimizer 任务持久化

`POST /api/agent/run` 启动一个 `claude --print --stream-json` 子进程，
通过 `/ws/agent/{job_id}` 推送事件，并把每次状态变更持久化到
`~/.quantforge/dashboard/agent_jobs/{job_id}.json`。

- **单任务保留**：新建 job 会先清掉旧文件。
- **重启恢复**：后端启动时，持久化里 `running` / `pending` 的任务被
  标为 `failed` 加 "Backend restarted" 提示，**同时根据捕获的 PID 给
  孤儿子进程发 SIGTERM**，避免它继续吃 CPU。
- **前端 404 自愈**：`useAgentStatus` 收到 404 后停止轮询，Optimizer
  页面自动调 `resetAgent()`，UI 不会卡在一个不存在的任务上。

### Evolving Mode — 自主策略调优 → 部署闭环

一套子系统，按周期自动 re-optimise 策略、跑 shadow/paper 对比、把 `pause`/
`reduce` 写到一个 control 文件里，Pine 实盘引擎启动时读取并执行。**默认
关闭。**

```
news_collector       拉 RSS / 交易所状态事件
   ↓
bot_preflight        起飞前体检：job 文件、注册表、policy
   ↓
auto_tune_scheduler → eval/auto_tune    重优化、产出候选 .pine + 指标
   ↓
deployment_pipeline  注册候选；PAPER → SHADOW 状态机
   ↓
paper_shadow_runner + paper_ledger      记录 paper vs shadow vs promoted 的 PnL
   ↓
risk_control         日内亏损 / drawdown / 连亏 闸门
   ↓
trading_control      写 ~/.quantforge/trading_control.json action
   ↓
PineLiveEngine.start() 读 trading_control → pause/reduce/resume
   ↓
audit_report         汇总所有证据为 JSON + Markdown
   ↓
alerts               cycle 失败或风控动作时 webhook / JSONL 推送
```

**主开关**：`~/.quantforge/evolving.json` ——
`{ enabled: bool, strategies: [str], updated_at }`。由
`quantforge/evolving/switch.py` 管理（以 `quantforge.evolving` 形式 re-export）。CLI 和 Web UI 都通过
`evolving.is_enabled(strategy)` 做闸门。

**启用方式**：
```bash
quantforge-cli bot evolving status                       # 查看
quantforge-cli bot evolving enable --strategy ema_crossover
quantforge-cli bot cycle ema_crossover                   # 跑一轮 cycle
quantforge-cli bot status ema_crossover                  # 快照
quantforge-cli bot evolving disable                      # 关闭，回到默认安全状态
```

**Web API**：`GET/POST /api/bot/evolving`、`GET /api/bot/status`、
`GET /api/bot/cycle/{strategy_id}`。顶栏的 `EvolvingBadge` 组件显示
主开关 + 一键切换。

**Pine 实盘引擎集成**：
`quantforge/pine/live/engine.py::PineLiveEngine.start()` 先调
`evolving.is_enabled(strategy_name)`。打开时读
`TradingControl().get_action(strategy_name)`：
- `pause` → 抛 `RuntimeError`，拒绝启动。
- `reduce` → 启动前 `position_size_usdt` 减半。
- `resume` / `observe` → 不做事。

主开关关闭时，整段 control 文件被忽略。

**Cron 自动托管**：`bot evolving enable` 会在 crontab 里加一段带标记的
块；`bot evolving disable` 会撤掉。要跳过自动托管：enable 加 `--no-cron`、
disable 加 `--keep-cron`。也可独立用 `bot cron {install, uninstall, status}`
明确管理。

托管的块长这样：
```cron
# >>> quantforge-evolving-cron (managed; do not edit) >>>
*/30 * * * * cd /repo && /venv/bin/quantforge-cli bot cycle ema_crossover --ops-dir ~/.quantforge/ops >> ~/.quantforge/ops/cron.log 2>&1
# <<< quantforge-evolving-cron <<<
```
只有两个 marker 之间的行受管理，crontab 里你自己写的其他行不会被动。
实现：`quantforge/evolving/cron_helper.py`。

**持久化布局**（`~/.quantforge/`）：
- `evolving.json` — 主开关
- `trading_control.json` — 按策略：`{action, reasons, score, updated_at}`
- `ops/auto_tune_jobs.json` — job 定义
- `ops/deployments.json` — 版本注册表（promoted/paper/shadow）
- `ops/paper_ledger.{json,sqlite}` — paper/shadow 仓位簿
- `ops/{cycle,status,audit,risk}.json` — 最近一次 cycle 的产出
- `ops/alerts.jsonl` — 追加式告警日志

### Grid Search 进度

`quantforge/pine/optimize.py::run_optimization()` 接受 `progress_cb`。
后端在 `_run_pine_optimize(req, job_id)` 里挂上去，把
`progress = { completed, total, avg_secs_per_combo, elapsed_secs }` 写回
in-memory job，WS 每秒推完整 status。前端渲染进度条 + "≈ 2m 15s 剩余 ·
1.23s / combo"。

## Pine Script 引擎 (`quantforge/pine/`)

TradingView 兼容 Pine Script v5 的解析器 + 解释器 + 转译器 + 实盘引擎 +
优化器。

### Pine 策略 (`quantforge/pine/strategies/`)

当前 `.pine` 文件（`/api/strategies` 会列出）：
`bb_squeeze`, `bb_squeeze_v2`, `bollinger_band`, `bollinger_band_v4`,
`dual_regime`, `ema_crossover`, `ema_crossover_v2`, `ema_crossover_v3`,
`hurst_kalman`, `macd_trend`, `momentum_adx`, `rsi_momentum`,
`sma_trend`。

AI 优化产出的 `.pine` 文件存放在 `quantforge/pine/strategies/optimized/`
（目前 `/api/strategies` 不暴露 — 作为 checkpoint 保留，不算 live 候选）。

测试 fixture 在 `test/pine/fixtures/`：
`ema_cross.pine`, `rsi_strategy.pine`, `rsi_mean_revert.pine`,
`macd_cross.pine`, `bb_strategy.pine`, `ema_cross_5_13.pine`。

### Pine CLI

```bash
# Backtest
python -m quantforge.pine.cli backtest my.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --warmup-bars 500

# Grid 优化（对 input.int / input.float 的范围做网格搜索）
python -m quantforge.pine.cli optimize my.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --metric sharpe --top 10 --json results.json

# 转译成自包含 Python（运行时不依赖 Pine 引擎）
python -m quantforge.pine.cli transpile my.pine --output strategy.py

# Live demo/sandbox
python -m quantforge.pine.cli live my.pine --exchange okx --demo --symbol BTC/USDT:USDT --timeframe 1h

# Live 实盘（需要 --confirm-live 兜底）
python -m quantforge.pine.cli live my.pine --exchange okx --no-demo --confirm-live --symbol BTC/USDT:USDT --timeframe 1h --leverage 5
```

### Pine 转译器

`quantforge/pine/transpiler/codegen.py` 生成与 Pine 解释器逐 bit 一致的
自包含 Python 代码。**TA 映射**：`ta.ema → _EMACalc`、`ta.sma →
_SMACalc`、`ta.rsi → _RSICalc`（Wilder/RMA 平滑）、`ta.macd →
_MACDCalc`、`ta.atr → _ATRCalc`、`ta.adx → _ADXCalc`、`ta.bb →
_BBCalc`、`ta.stoch → _StochCalc`、`ta.stdev → _StdevCalc`、
`ta.crossover/crossunder → _crossover/_crossunder`（带前一根 bar 跟踪）、
`ta.highest/lowest → _HighestCalc/_LowestCalc`、`ta.change →
_ChangeCalc`。**策略映射**：`strategy.entry → tracker.queue_entry()`、
`strategy.close → tracker.queue_close()`（订单在下一根 bar 的 open 处
成交）。**21 个 parity 测试**锁死这一行为。

### Pine 实盘引擎

回测和实盘**共用同一个解释器**，模式之间不做转译。关键文件：
- `quantforge/pine/live/engine.py` — `PineLiveEngine`：warmup + 实时
  K 线循环，按 bar 精确对时
- `quantforge/pine/live/order_bridge.py` — `OrderBridge`：拦截 Pine
  `strategy.entry/close/exit` 回调，下到 ccxt
- `quantforge/pine/live/connector.py` — `CcxtConnector`（处理 demo
  开关、从 `settings` 读 API key、调用 `set_leverage` /
  `create_{market,limit}_order` / `fetch_positions` / `fetch_balance`）

实盘表现按 bar 落盘到
`~/.quantforge/live/{strategy_name}/live_performance.json`；后端的
`_find_perf_files()` 扫文件，每 3s 通过 `/ws/live/performance` 推送。

### 支持的 `ta.*` 函数
`ta.sma`, `ta.ema`, `ta.rma`, `ta.rsi`, `ta.atr`, `ta.adx`, `ta.macd`,
`ta.bb`, `ta.stoch`, `ta.stdev`, `ta.crossover`, `ta.crossunder`,
`ta.highest`, `ta.lowest`, `ta.change`, `ta.tr`。

## Streaming 指标原语 (`quantforge/indicators/streaming.py`)

| 类 | 说明 |
|---|---|
| `StreamingEMA(period)` | 指数移动平均 |
| `StreamingSMA(period)` | 简单移动平均（滚动窗口） |
| `StreamingATR(period)` | 平均真实波幅（Wilder 平滑） |
| `StreamingROC(period)` | 变化率 |
| `StreamingADX(period)` | 平均趋向指数 |
| `StreamingBB(period, multiplier)` | 布林带 |
| `StreamingRSI(period)` | RSI（Wilder/RMA 平滑） |

共享接口：`.value`（`Optional[float]`）、`.update(...)`、`.reset()`。被
DSL（`quantforge/dsl/indicators.py`）和 Pine 转译器的 TA 计算器复用。

## 声明式 DSL (`quantforge/dsl/`)

```python
from quantforge.dsl import Strategy, Param

class EMACross(Strategy):
    name = "decl_ema_crossover"
    timeframe = "15m"
    fast_period = Param(12, min=5, max=30, step=2)
    slow_period = Param(26, min=15, max=60, step=5)

    def setup(self):
        self.ema_fast = self.add_indicator("ema", self.fast_period)
        self.ema_slow = self.add_indicator("ema", self.slow_period)

    def on_bar(self, bar):
        if self.ema_fast.crossover(self.ema_slow): return self.BUY
        if self.ema_fast.crossunder(self.ema_slow): return self.SELL
        return self.HOLD
```

`add_indicator` 支持的名称：`ema`, `sma`, `rsi`, `atr`, `adx`, `bb`,
`roc`。35 个测试。

## 支持的交易所

| Exchange | ccxt id | Maker | Taker |
|---|---|---|---|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

## Secrets 配置

API key 存在 **`.keys/.secrets.toml`**（gitignored），通过 dynaconf 在
`quantforge/constants.py` 中加载。import 时 `_check_secrets_file_perms()`
会校验权限为 `0600`，否则自动 chmod 并发警告。文件结构：

```toml
[BITGET.LIVE]
API_KEY = "..."
SECRET = "..."
PASSPHRASE = "..."

[OKX.LIVE.ACCOUNT1]
API_KEY = "..."
SECRET = "..."
PASSPHRASE = "..."

[BINANCE.TESTNET]
API_KEY = "..."
SECRET = "..."
```

每个交易所的 key 读取逻辑在
`quantforge/pine/live/connector.py::_create_exchange()` —— 一次读出，
应用到 `ccxt.<id>({apiKey, secret, password})`。

## Symbol 格式

`{base}{quote}-{instrument_type}.{exchange}`，如
`BTCUSDT-PERP.BINANCE`。CLI / API 也直接接受 ccxt 各交易所的格式
（`BTC/USDT:USDT`）。

## 统一 CLI (`quantforge-cli`)

Click 命令组，每个 Web 路由都有对应的 CLI 子命令。无状态操作直接读盘，
有状态的打到 `$QF_API_URL`（默认 `http://127.0.0.1:8000/api`）。

| 命令 | Web 对应 | 模式 |
|---|---|---|
| `strategies list / show <n> / source <n> / rename <old> <new>` | `/strategies*` | 文件系统 |
| `exchanges list` | `/exchanges` | 静态 |
| `engines list [--via-server]` | `/live/engines` | 文件 或 HTTP |
| `engines start <pine> [--via-server]` | `/live/start` | 前台 或 HTTP |
| `engines stop <id>` | `/live/stop/{id}` | HTTP |
| `engines performance [strategy]` | `/live/performance` | 文件 |
| `agent skills` | `/agent/skills` | 文件系统 |
| `agent run --skill X --strategy Y [--via-server]` | `/agent/run` | 前台子进程 或 HTTP |
| `agent status <id>` / `stop <id>` | `/agent/{id}*` | HTTP |
| `backtest <pine>` / `optimize <pine>` / `live <pine>` | `/backtest/run`、`/optimize/run`、`/live/start` | 包装 `quantforge.pine.cli` |

所有列出类命令支持 `--json`。Pine 名字会从 `quantforge/pine/strategies/`
自动解析。源码在
`quantforge/cli/commands/{strategies,exchanges,engines,agent,pine}_cmd.py`，
HTTP 客户端在 `_http.py`。

## AI Optimizer Skill (`.claude/skills/quantforge-optimizer/`)

Web UI "AI Optimize" 模式调用的项目级 Claude skill。实现 TiMi 风格的
闭环数学反思：先回测、再用数学约束反推参数、再回测，迭代收敛。在仓库
内维护，方便迭代后做 A/B 评估。

## TiMi 优化器 A/B 框架 (`eval/optimizer_ab/`)

气隙评估框架，用来比较优化器 skill 的不同变体。每个 trial =
`(method, strategy, regime, seed)`；`runner.py` 在隔离 staged skill 目录
里只用 *train* 窗口跑 Claude Code，然后 `holdout_eval.py` 把优化产出的
`.pine` 在 regime 的 *holdout* 窗口跑一遍，agent 始终看不到 OOS 数据。

| 文件 | 角色 |
|---|---|
| `test_set.yaml` | 冻结的 3 档策略划分 × regime × seed |
| `methods/<name>/SKILL.md` | 每个待测方法一份 |
| `runner.py` | 单个 trial：staged skill、调 Claude、捕获 `FINAL_OUTPUT:` |
| `holdout_eval.py` | 按 bar 时间戳过滤 equity/trades；warmup 段不计入 |
| `orchestrate.py` | 矩阵循环；resume key 是 `cell_id + "__"`（防止 seed=1 是 seed=10 的前缀） |
| `analyze.py` | 各方法的聚合 + 配对 Wilcoxon + bootstrap 95% CI |
| `rebuild_csv.py` | 从 trial JSON 重建 CSV，不重跑 runner |
| `cross_review.py` | 跨模型评审变体 |

**气隙不变式**：agent prompt 把 `--start --end` 钉在 train 窗口；
`stage_skill` 把 SKILL.md / scripts / references 里的硬编码日期重写到
本次 trial 的训练窗口；OOS 指标只在 `time >= start_unix` 的 bar 上算；
每个 trial 的 `optimization_log.jsonl` 被清掉，避免跨 run 学习。

**已知局限**：Claude CLI 没暴露 `--seed`，所以 `seeds: [1, 2, 3]` 是
*replicate 编号*，不是可复现的随机种子 — 用 seed 的 median ± bootstrap
CI 来报告，不要给单点估计。

## 约定

- **import 路径**：一律绝对（`from quantforge.pine ...`），不用相对。
- **策略写在 `.pine` 文件里**，不要写到 `.py`。DSL 只用来做原型。
- **不要写单交易所手写连接器代码** — 走 ccxt 统一接口。如果功能缺，
  monkey-patch ccxt 实例，不要平行写另一套 adapter。

## 工作流规则

- 每次代码改动之后：更新 `CLAUDE.md` 和 `CLAUDE_CN.md`，commit 并 push
  到 `dev` 分支。

## Ruff 用法

- Lint：`uvx ruff check`
- Format：`uvx ruff format`

# CLAUDE.md（中文版）

本文件为 Claude Code (claude.ai/code) 在本代码库中工作时提供指引。

## 项目概述

QuantForge 是一个基于 Python 3.11+ 构建的专业级量化交易平台，专注于跨多交易所的高性能、低延迟交易。采用模块化、事件驱动架构，核心组件由 Rust 驱动，以实现极致性能。

**所有交易策略均以 Pine Script (.pine) 文件表达。** `quantforge/` 包是唯一的代码源。

## 开发命令

### 依赖安装与环境配置
```bash
# 使用 uv 安装（本项目使用的包管理器）
uv sync

# 安装开发依赖
uv sync --group dev

# 安装 pre-commit hooks（贡献代码时必须安装）
uv add --dev pre-commit
pre-commit install
```

### 测试
```bash
# 运行所有测试（138 Pine/DSL + 核心测试）
uv run pytest

# Pine Script 测试（103个：解释器、转译器一致性、实盘引擎、优化器）
uv run pytest quantforge/pine/tests/ -v

# DSL 测试（35个）
uv run pytest quantforge/dsl/tests/ -v

# 核心框架测试
uv run pytest test/core/

# 测试配置：pytest.ini 启用 asyncio_mode = auto
```

### 代码质量
```bash
# 代码检查与格式化（通过 ruff）
uvx ruff check
uvx ruff format
```

### 开发基础设施
```bash
# 启动 Redis、PostgreSQL、Loki 日志服务
docker-compose up -d

# 清除日志文件
./clear.sh

# 进程管理（生产环境）
pm2 start ecosystem.config.js
```

## 架构概览

### 核心组件
- **Engine（引擎）**：管理所有交易系统的中央协调器（`quantforge/engine.py`）
- **Strategy（策略）**：支持多种执行模式的交易逻辑基类（`quantforge/strategy.py`）
- **Pine 引擎**：Pine Script 解释器 + 实盘交易引擎（`quantforge/pine/`）
- **Connectors（连接器）**：交易所专用的公共（行情数据）和私有（交易）连接器
- **EMS**（执行管理系统）：订单提交与执行
- **OMS**（订单管理系统）：订单状态跟踪与管理
- **Cache（缓存）**：高性能数据缓存层（`quantforge/core/cache.py`）
- **Registry（注册表）**：订单和组件跟踪（`quantforge/core/registry.py`）

### 交易所集成
每个交易所在 `quantforge/exchange/{exchange}/` 下遵循统一模式：
- **PublicConnector**：行情数据 WebSocket 流
- **PrivateConnector**：账户数据和订单执行
- **EMS/OMS**：交易所专用订单管理
- **ExchangeManager**：协调连接器和各子系统

支持的交易所：
- **主要**：Binance、Bybit、OKX（完整实现）
- **其他**：Bitget、Hyperliquid

### 性能优化
- **uvloop**：高性能事件循环（比原生 asyncio 快 2-4 倍）
- **picows**：基于 Cython 的 WebSocket 库（C++ 级性能）
- **msgspec**：超快速序列化/反序列化
- **nautilus-trader**：Rust 驱动的 MessageBus 和 Clock 组件

## 关键文件位置

### 核心框架
- `quantforge/engine.py` - 主交易引擎
- `quantforge/strategy.py` - 策略基类
- `quantforge/config.py` - 配置管理
- `quantforge/schema.py` - 数据结构与模式
- `quantforge/indicator.py` - 技术指标框架

### 流式指标
- `quantforge/indicators/streaming.py` - StreamingEMA、StreamingSMA、StreamingATR、StreamingADX、StreamingROC、StreamingBB、StreamingRSI

### 基类
- `quantforge/base/connector.py` - 连接器基类实现
- `quantforge/base/ems.py` - 执行管理基类
- `quantforge/base/oms.py` - 订单管理基类

### 交易所实现
每个交易所目录包含：
- `connector.py` - 公共/私有连接器
- `ems.py` - 交易所专用执行管理
- `oms.py` - 交易所专用订单管理
- `schema.py` - 交易所数据结构
- `websockets.py` - WebSocket 实现
- `rest_api.py` - REST API 客户端

### 配置与数据
- `quantforge/constants.py` - 枚举和常量
- `quantforge/backends/` - 数据库后端（Redis、PostgreSQL、SQLite）

## 环境配置

复制 `env.example` 为 `.env` 并进行配置：
```bash
# Redis 配置
QUANTFORGE_REDIS_HOST=127.0.0.1
QUANTFORGE_REDIS_PORT=6379
QUANTFORGE_REDIS_DB=0
QUANTFORGE_REDIS_PASSWORD=your_redis_password

# PostgreSQL 配置
QUANTFORGE_PG_HOST=localhost
QUANTFORGE_PG_PORT=5432
QUANTFORGE_PG_USER=postgres
QUANTFORGE_PG_PASSWORD=your_postgres_password
QUANTFORGE_PG_DATABASE=postgres
```

## 交易对格式

所有交易对遵循以下命名规则：`{基础货币}{计价货币}-{合约类型}.{交易所}`

示例：
- `BTCUSDT-PERP.BINANCE`（Binance 永续合约）
- `BTCUSDT-PERP.OKX`（OKX 永续合约）
- `BTCUSDT-PERP.BYBIT`（Bybit 永续合约）

## 配置管理

使用 `dynaconf` 进行基于环境的配置管理：
- API 凭证存储在 `settings` 系统中
- 通过 `.env` 文件管理环境变量
- 交易所账户类型指定测试网/主网及账户类别

## 贡献指南

源自 CONTRIBUTING.md：
1. 实现更改前先创建 GitHub issue
2. 从 main 分支 fork 并保持同步
3. 安装 pre-commit hooks（强制要求）
4. 小型、聚焦的 pull request，描述清晰
5. 在 PR 描述中引用 GitHub issue
6. 所有 PR 以 main 分支为目标

## 基础设施服务

开发环境技术栈包括：
- **Redis**：数据缓存和发布/订阅消息
- **PostgreSQL**：持久化数据存储
- **Grafana Loki**：集中式日志
- **Promtail**：日志收集代理

启动方式：`docker-compose up -d`

## 开发规范

### 导入规范
- 始终使用绝对路径导入

### 连接器杠杆设置
- `PrivateConnectorConfig.leverage` 设置杠杆倍数；`leverage_symbols` 可选地限制应用于哪些交易对。
- 杠杆在 `strategy.on_start()` 之后通过 `engine._apply_leverage()` 应用，因此只针对策略实际订阅的交易对。
- 优先级：显式 `leverage_symbols` 配置 → 自动检测策略交易对 → 跳过（无全品种兜底）。

## 流式指标原语（`quantforge/indicators/streaming.py`）

| 类名 | 描述 |
|------|------|
| `StreamingEMA(period)` | 指数移动平均线 |
| `StreamingSMA(period)` | 简单移动平均线（滚动窗口） |
| `StreamingATR(period)` | 平均真实波幅（Wilder 平滑） |
| `StreamingROC(period)` | 变化率 |
| `StreamingADX(period)` | 平均方向指数 |
| `StreamingBB(period, multiplier)` | 布林带（SMA ± multiplier × σ） |
| `StreamingRSI(period)` | 相对强弱指数（Wilder 平滑） |

所有原语共享：`.value` 属性、`.update()` 返回 `Optional[float]`、`.reset()` 方法。

使用者：`quantforge/dsl/indicators.py`、Pine 转译器 TA 计算器。

## Pine Script 引擎

Pine Script 引擎（`quantforge/pine/`）提供 TradingView 兼容的 Pine Script v5 解析器、解释器和转译器。**这是主要的策略层。**

### Pine 策略

所有交易策略以 `.pine` 文件形式存放在 `quantforge/pine/strategies/`：

| 策略 | 文件 | 描述 |
|------|------|------|
| 动量 ADX | `momentum_adx.pine` | ROC、EMA、ADX 过滤、ATR 追踪止损的趋势跟踪 |
| 布林带 | `bollinger_band.pine` | 带趋势 SMA 过滤的布林带均值回归 |
| 双状态 | `dual_regime.pine` | 自适应：趋势环境用动量，震荡环境用均值回归 |
| SMA 趋势 | `sma_trend.pine` | 仅做多的日线 SMA 趋势跟踪 |
| Hurst Kalman | `hurst_kalman.pine` | 统计套利近似（EMA 代理 Kalman 滤波） |
| EMA 交叉 | `ema_crossover.pine` | 简单 EMA 交叉（快/慢） |

测试固件在 `quantforge/pine/tests/fixtures/`：`ema_cross.pine`、`rsi_strategy.pine`、`rsi_mean_revert.pine`、`macd_cross.pine`、`bb_strategy.pine`、`ema_cross_5_13.pine`

### Pine CLI

```bash
# 在交易所数据上回测 .pine 文件
python -m quantforge.pine.cli backtest my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --warmup-days 60

# 优化输入参数（对 input.int/input.float 范围进行网格搜索）
python -m quantforge.pine.cli optimize my_strategy.pine --symbol BTC/USDT:USDT --exchange bitget --timeframe 15m --start 2026-01-01 --end 2026-03-12 --metric sharpe --top 10 --json results.json

# 将 Pine Script 转译为独立 Python
python -m quantforge.pine.cli transpile my_strategy.pine --output strategy.py
python strategy.py  # 独立运行 — 不依赖 Pine 解释器

# 实盘交易（模拟/纸上交易）
python -m quantforge.pine.cli live my_strategy.pine --exchange bitget --demo --symbol BTC/USDT:USDT --timeframe 15m

# 实盘交易（真金白银）
python -m quantforge.pine.cli live my_strategy.pine --exchange bitget --no-demo --confirm-live --symbol BTC/USDT:USDT --timeframe 15m
```

### Pine 转译器

转译器（`quantforge/pine/transpiler/codegen.py`）生成**自包含的 Python 脚本**：
- 内嵌与 TradingView 完全一致的 TA 计算器类（EMA 使用 SMA 种子、RSI 使用 Wilder/RMA 平滑等）
- 使用下一根 K 线开盘价执行语义跟踪仓位和执行订单
- 通过 ccxt 获取 OHLCV 数据或接受 `list[list]` 格式
- 独立计算盈亏 — 不依赖 Pine 解释器

**TA 映射**：`ta.ema` → `_EMACalc`、`ta.sma` → `_SMACalc`、`ta.rsi` → `_RSICalc`、`ta.macd` → `_MACDCalc`、`ta.stdev` → `_StdevCalc`、`ta.atr` → `_ATRCalc`、`ta.adx` → `_ADXCalc`、`ta.bb` → `_BBCalc`、`ta.stoch` → `_StochCalc`、`ta.crossover`/`ta.crossunder` → `_crossover`/`_crossunder`、`ta.highest`/`ta.lowest` → `_HighestCalc`/`_LowestCalc`、`ta.change` → `_ChangeCalc`

**一致性保证**：转译后的代码在相同数据上产生与 Pine 解释器完全一致的交易和盈亏。通过 21 个一致性测试覆盖 6 个策略验证。

### 支持的 ta.* 函数

`ta.sma`, `ta.ema`, `ta.rma`, `ta.rsi`, `ta.atr`, `ta.adx`, `ta.macd`, `ta.bb`, `ta.stoch`, `ta.stdev`, `ta.crossover`, `ta.crossunder`, `ta.highest`, `ta.lowest`, `ta.change`, `ta.tr`

### Web UI 架构

所有回测和优化逻辑统一在主回测模块中：
- `web/backend/jobs.py` — 共享工具（`_fetch_ohlcv`、`_resolve_pine_source`、`_resolve_date_range`）及回测/优化任务运行器
- `web/backend/routers/backtest.py` — `/backtest/run`（POST，接受策略文件名或 Pine 源码）、`/backtest/{id}`（GET，轮询状态）
- `web/backend/routers/optimize.py` — `/optimize/run`（POST，Pine 网格搜索）、`/optimize/{id}`（GET，轮询状态）
- `web/backend/routers/pine.py` — 仅工具端点：`/pine/parse` 和 `/pine/transpile`
- `web/backend/routers/strategies.py` — `/strategies`（列出 Pine 文件及解析的输入参数）、`/exchanges`
- 前端页面：`Backtest.tsx`（策略选择器+图表）、`PinePage.tsx`（Pine 编辑器+通过任务轮询回测）、`Optimizer.tsx`
- 路由：Web UI 中的 `/backtest`、`/pine`、`/optimizer`

### Pine 实盘交易引擎

Pine 解释器**直接**作为实盘交易引擎运行——无需转译。

**架构：**
```
Pine Script (.pine 文件)
     ↓
Pine 解释器（回测和实盘使用同一引擎）
     ↓  喂入实时确认 K 线
QuantForge 交易所连接器（通过 ccxt）
     ↓  strategy.entry/close 信号 → 真实订单
交易所
```

**关键文件：**
- `quantforge/pine/live/engine.py` — `PineLiveEngine`：预热 + 实时 K 线循环
- `quantforge/pine/live/order_bridge.py` — `OrderBridge`：Pine 信号 → 交易所订单
- `quantforge/pine/live/connector.py` — 预热 K 线获取 + `CcxtConnector`
- `quantforge/pine/optimize.py` — 参数网格搜索优化

**增量执行 API**：
- `init_incremental(script)` — 解析声明，重置指标状态
- `process_bar(bar) → list[Order]` — 喂入一根 K 线，返回新订单
- `finalize() → BacktestResult` — 关闭剩余仓位，返回结果

**一致性保证：** 11 个测试验证增量逐根执行产生与批量执行完全相同的交易和权益曲线。

**实时性能仪表板集成：**
- `DemoTracker.to_dict()` 将盈亏、交易、回撤序列化为 `LivePerformanceOut` 兼容 JSON
- `PineLiveEngine._flush_performance()` 每根 K 线后写入 `~/.quantforge/live/{策略名}/live_performance.json`
- Web 后端 `_find_perf_files()` 通过 `rglob("live_performance.json")` 发现这些文件
- WebSocket 端点 `/ws/live/performance` 每 3 秒推送更新

### Pine 测试

```bash
uv run pytest quantforge/pine/tests/ -v  # 89个测试（55 解释器/解析器 + 11 实时引擎 + 16 优化器 + 7 TV对齐）
```

## 声明式策略 DSL (`quantforge/dsl/`)

简化的声明式 Python API，用 ~15-30 行代码定义交易策略。使用 `quantforge/indicators/` 中的流式指标。

### 快速开始

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
        if self.ema_fast.crossover(self.ema_slow):
            return self.BUY
        if self.ema_fast.crossunder(self.ema_slow):
            return self.SELL
        return self.HOLD
```

### 包结构

```
quantforge/dsl/
├── __init__.py          # 公共API: Strategy, Param, Bar, Indicator
├── api.py               # Strategy基类, Param描述符, Bar数据类
├── indicators.py        # 指标包装器（crossover/crossunder/历史/回溯）
├── registry.py          # 通过元类自动注册
├── backtest.py          # 简单回测器，支持1根K线信号延迟
├── runner.py            # CLI 运行器
├── examples/            # 5个示例策略
└── tests/
    └── test_new_api.py  # 35个测试
```

### 支持的指标

`"ema"`, `"sma"`, `"rsi"`, `"atr"`, `"adx"`, `"bb"`, `"roc"` — 全部复用 `quantforge/indicators/streaming.py` 中的 `StreamingXXX` 类

### DSL 测试

```bash
uv run pytest quantforge/dsl/tests/ -v  # 35个测试
```

## 回测数据基础设施

### 本地数据缓存与多源验证

`quantforge/backtest/data/database.py` 提供 SQLite 缓存层：
- **KlineDatabase**: 存储 OHLCV 数据至 `~/.quantforge/data/klines.db`
- API: `save()`, `load()`, `has_data()`, `get_gaps()`, `stats()`

`quantforge/backtest/data/cached_provider.py` 提供智能缓存 + 验证：
- **CachedDataProvider**: `fetch()` 先查缓存，仅拉取缺失区间
- **ValidatedData**: `fetch_and_validate()` 跨交易所对比数据

### 蒙特卡洛模拟与压力测试

`quantforge/backtest/simulation/` 子模块提供策略稳健性评估的统计模拟功能：

| 模块 | 类 | 用途 |
|------|-----|------|
| `bootstrap.py` | `BlockBootstrap` | 分块自助法重采样 |
| `monte_carlo.py` | `GBMGenerator` | 几何布朗运动路径生成 |
| `monte_carlo.py` | `JumpDiffusionGenerator` | Merton 跳跃扩散 |
| `stress_test.py` | `StressTestGenerator` | 崩盘、暴涨、波动率放大情景 |
| `report.py` | `SimulationReport` | 分布统计、置信区间 |

### 支持的交易所

| 交易所 | CCXT ID | Maker 费率 | Taker 费率 |
|--------|---------|-----------|-----------|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

## Claude Code 记忆

### 工作规范
- 每次代码变更后同步更新 CLAUDE.md 和 CLAUDE_CN.md，然后 commit 并 push 到 dev 分支

### CLI 使用注意事项
- 不要在 Claude Code 中运行 quantforge-cli moniter

### Ruff 使用
- 使用 `uvx ruff check` 检查当前目录所有文件
- 使用 `uvx ruff format` 格式化当前目录所有文件

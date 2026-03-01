# CLAUDE.md（中文版）

本文件为 Claude Code (claude.ai/code) 在本代码库中工作时提供指引。

## 项目概述

NexusTrader 是一个基于 Python 3.11+ 构建的专业级量化交易平台，专注于跨多交易所的高性能、低延迟交易。采用模块化、事件驱动架构，核心组件由 Rust 驱动，以实现极致性能。

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
# 运行所有测试（支持异步）
uv run pytest

# 运行特定测试模块
uv run pytest test/core/
uv run pytest test/core/test_entity.py

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
- **Engine（引擎）**：管理所有交易系统的中央协调器（`nexustrader/engine.py`）
- **Strategy（策略）**：支持多种执行模式的交易逻辑基类（`nexustrader/strategy.py`）
- **Connectors（连接器）**：交易所专用的公共（行情数据）和私有（交易）连接器
- **EMS**（执行管理系统）：订单提交与执行
- **OMS**（订单管理系统）：订单状态跟踪与管理
- **Cache（缓存）**：高性能数据缓存层（`nexustrader/core/cache.py`）
- **Registry（注册表）**：订单和组件跟踪（`nexustrader/core/registry.py`）

### 交易所集成
每个交易所在 `nexustrader/exchange/{exchange}/` 下遵循统一模式：
- **PublicConnector**：行情数据 WebSocket 流
- **PrivateConnector**：账户数据和订单执行
- **EMS/OMS**：交易所专用订单管理
- **ExchangeManager**：协调连接器和各子系统

支持的交易所：
- **主要**：Binance、Bybit、OKX（完整实现）
- **其他**：Bitget、Hyperliquid

### 策略执行模式
1. **事件驱动**：响应市场事件（`on_bookl1`、`on_trade`、`on_kline`）
2. **定时器驱动**：通过 `schedule()` 方法定期执行
3. **信号驱动**：自定义信号处理（`on_custom_signal`）

### 性能优化
- **uvloop**：高性能事件循环（比原生 asyncio 快 2-4 倍）
- **picows**：基于 Cython 的 WebSocket 库（C++ 级性能）
- **msgspec**：超快速序列化/反序列化
- **nautilus-trader**：Rust 驱动的 MessageBus 和 Clock 组件

## 关键文件位置

### 核心框架
- `nexustrader/engine.py` - 主交易引擎
- `nexustrader/strategy.py` - 策略基类
- `nexustrader/config.py` - 配置管理
- `nexustrader/schema.py` - 数据结构与模式
- `nexustrader/indicator.py` - 技术指标框架

### 基类
- `nexustrader/base/connector.py` - 连接器基类实现
- `nexustrader/base/ems.py` - 执行管理基类
- `nexustrader/base/oms.py` - 订单管理基类

### 交易所实现
每个交易所目录包含：
- `connector.py` - 公共/私有连接器
- `ems.py` - 交易所专用执行管理
- `oms.py` - 交易所专用订单管理
- `schema.py` - 交易所数据结构
- `websockets.py` - WebSocket 实现
- `rest_api.py` - REST API 客户端

### 策略信号层
- `strategy/strategies/_base/streaming.py` - 流式指标原语（EMA、SMA、ATR、ADX、ROC、BB、RSI）
- `strategy/strategies/{name}/signal_core.py` - 信号核心（每个策略的唯一真实来源）
- `strategy/strategies/_base/` - BaseSignalGenerator、TradeFilterConfig、注册辅助工厂、BaseQuantStrategy、PerformanceTracker、GenericStrategy、GenericIndicator、通用配置
- `strategy/strategies/{name}/` - 自包含策略包：signal_core.py、core.py、registration.py（必须）；indicator.py、live.py、configs.py（可选）
- `strategy/runner.py` - 支持 LiveConfig 的任意策略通用 CLI 运行器

### 配置与数据
- `nexustrader/constants.py` - 枚举和常量
- `nexustrader/backends/` - 数据库后端（Redis、PostgreSQL、SQLite）

## 环境配置

复制 `env.example` 为 `.env` 并进行配置：
```bash
# Redis 配置
NEXUS_REDIS_HOST=127.0.0.1
NEXUS_REDIS_PORT=6379
NEXUS_REDIS_DB=0
NEXUS_REDIS_PASSWORD=your_redis_password

# PostgreSQL 配置
NEXUS_PG_HOST=localhost
NEXUS_PG_PORT=5432
NEXUS_PG_USER=postgres
NEXUS_PG_PASSWORD=your_postgres_password
NEXUS_PG_DATABASE=postgres
```

## 自定义指标开发

指标支持使用历史数据自动预热：

```python
class CustomIndicator(Indicator):
    def __init__(self, period: int = 20):
        super().__init__(
            params={"period": period},
            name=f"Custom_{period}",
            warmup_period=period * 2,  # 需要的历史周期数
            warmup_interval=KlineInterval.MINUTE_1,  # 数据时间间隔
        )

    def handle_kline(self, kline: Kline):
        # 处理 K 线数据
        pass
```

在策略中注册指标：
```python
self.register_indicator(
    symbols="BTCUSDT-PERP.BINANCE",
    indicator=self.custom_indicator,
    data_type=DataType.KLINE,
    account_type=BinanceAccountType.USD_M_FUTURE_TESTNET,
)
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

## 统一信号核心架构

所有交易策略共享一个 **SignalCore** 模式，保证回测和实盘交易之间 100% 的代码一致性。信号逻辑存在于单一的共享类中——回测信号生成器和实盘指标都委托给它。

### 目录结构

```
strategy/strategies/_base/
├── streaming.py             # 流式指标原语（EMA、SMA、ATR、ADX、ROC、BB、RSI）
├── test_data.py             # 合成 OHLCV 数据生成器（供 registration.py 和测试共用）
├── __init__.py
├── signal_generator.py      # BaseSignalGenerator、TradeFilterConfig、列常量
├── registration_helpers.py  # 工厂函数：make_split_params_fn、make_mesa_dict_to_config 等
├── base_strategy.py         # BaseQuantStrategy：实盘策略共享基类
├── generic_indicator.py     # GenericIndicator：将任意 SignalCore 包装用于实盘交易
├── generic_strategy.py      # GenericStrategy：使用 LiveConfig 的通用实盘策略
├── generic_configs.py       # 通用配置加载器（替代各策略的 configs.py）
├── performance.py           # 实盘/模拟交易的 PerformanceTracker
├── paper_validate.py        # 模拟交易验证工具

strategy/runner.py               # 支持 LiveConfig 的任意策略通用 CLI 运行器

strategy/strategies/{name}/
├── signal_core.py       # SignalCore 类（信号逻辑唯一真实来源）
├── core.py              # 策略配置 dataclass
├── registration.py      # 策略注册（自动发现）+ LiveConfig + ParityTestConfig
├── indicator.py         # （可选）用于复杂策略的自定义指标
├── live.py              # （可选）需要 on_kline 覆盖的自定义实盘策略
├── configs.py           # （可选）自定义配置加载器（generic_configs.py 可替代此文件）

strategy/backtest/registry.py          # StrategyRegistration、ParityTestConfig、LiveConfig、HeatmapConfig
test/strategy/parity_factory.py        # 测试工厂：make_parity_test_class()
test/strategy/test_all_parity.py       # 通过注册表自动发现所有策略（无需手动编辑）
```

### 信号常量

所有核心使用相同的整数信号值：
- `HOLD = 0` — 不操作
- `BUY = 1` — 开多 / 平空
- `SELL = -1` — 开空 / 平多
- `CLOSE = 2` — 平仓

### 三方法 API

每个 `SignalCore` 类暴露三个方法：

| 方法 | 使用者 | 描述 |
|------|--------|------|
| `update(close, high, low, ...)` | 回测 + 实盘（实盘模式） | 更新指标 + 返回包含完整仓位管理的信号 |
| `update_indicators_only(close, high, low, ...)` | 实盘（预热模式） | 仅更新指标，不涉及信号/仓位逻辑 |
| `get_raw_signal()` | 实盘（预热模式） | 基于当前指标值的无状态信号计算 |

实盘指标运行在**双模式**下：预热期间使用 `update_indicators_only()` + `get_raw_signal()` 避免产生虚假仓位状态；预热完成后通过 `enable_live_mode()` 切换为 `core.update()` 进行统一仓位管理。

### 流式指标原语（`streaming.py`）

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

### 信号核心 → 策略映射

| 核心类 | 配置 | 使用的指标 | 策略类型 |
|--------|------|-----------|----------|
| `MomentumSignalCore` | `MomentumConfig` | EMA×2、SMA、ATR、ROC | 趋势跟踪 |
| `EMASignalCore` | `EMAConfig` | EMA×2 | 趋势跟踪 |
| `BBSignalCore` | `BBConfig` | BB、SMA（趋势偏向） | 均值回归 |
| `RegimeEMASignalCore` | `RegimeEMAConfig` | EMA×2、ATR、ADX | 状态门控趋势 |
| `HurstKalmanSignalCore` | `HurstKalmanConfig` | KalmanFilter1D、Hurst、ZScore | 统计套利 |
| `VWAPSignalCore` | `VWAPConfig` | RSI、累积 VWAP、ZScore | 均值回归 |
| `FundingRateSignalCore` | `FundingRateConfig` | SMA、资金费率队列 | 资金费率套利（仅做空） |
| `DualRegimeSignalCore` | `DualRegimeConfig` | ADX、ROC、EMA×3、ATR、SMA、BB | 自适应状态切换 |
| `GridSignalCore` | `GridConfig` | SMA、ATR、动态网格层级 | 网格交易 |

### 仓位管理状态

每个核心跟踪：`position`（0/1/-1）、`entry_bar`、`entry_price`、`cooldown_until`、`signal_count`、`bar_index`。过滤参数：`min_holding_bars`（最小持仓K线数）、`cooldown_bars`（冷却K线数）、`signal_confirmation`（信号确认）。

### BaseSignalGenerator（`_base/signal_generator.py`）

通用信号生成器，替代所有策略各自的 `signal.py` 文件。策略间的差异通过构造函数参数编码：

```python
class BaseSignalGenerator:
    def __init__(self, config, filter_config, *, core_cls, update_columns,
                 core_extra_filter_fields=("signal_confirmation",),
                 pre_loop_hook=None, bar_hook=None):
```

**列常量**（传递给 `core.update()` 的 DataFrame 列）：
- `COLUMNS_CLOSE = ("close",)` — ema_crossover、bollinger_band、hurst_kalman
- `COLUMNS_CLOSE_HIGH_LOW = ("close", "high", "low")` — regime_ema、grid_trading
- `COLUMNS_CLOSE_HIGH_LOW_VOLUME = ("close", "high", "low", "volume")` — momentum、dual_regime、vwap

**TradeFilterConfig**（所有策略的基础过滤配置）：
```python
@dataclass
class TradeFilterConfig:
    min_holding_bars: int = 4
    cooldown_bars: int = 2
    signal_confirmation: int = 1
```

需要额外过滤字段的策略使用子类（如 `HurstKalmanFilterConfig` 增加 `only_mean_reversion`）。

**Hook 机制**（用于特殊策略）：
- `pre_loop_hook(core, data, generator)` — funding_rate：注入资金费率时间序列
- `bar_hook(core, data, i, arrays)` — vwap：注入 `day` 参数；funding_rate：注入时间参数

### 注册辅助工厂（`_base/registration_helpers.py`）

四个工厂函数通过 dataclass 内省自动生成样板代码：

| 函数 | 用途 |
|------|------|
| `make_split_params_fn(config_cls)` | 将混合参数字典拆分为 (config_kwargs, filter_kwargs) |
| `make_filter_config_factory(filter_config_cls, min_hold_formula=None)` | 为热力图扫描生成过滤配置 |
| `make_mesa_dict_to_config(config_cls, filter_config_cls, x_param, y_param, ...)` | 将 mesa 热力图结果转换为 StrategyConfig |
| `make_export_config(strategy_name, config_cls, filter_config_cls, ...)` | 从优化参数生成 Python 配置代码 |

### 新增策略流程

#### 最简版（通用运行器 — 仅需在 `strategies/{name}/` 下新建文件）：

1. **信号核心** — `strategy/strategies/{name}/signal_core.py`：实现 `{Name}SignalCore`，包含 `update()`、`update_indicators_only()`、`get_raw_signal()`
2. **配置** — `strategy/strategies/{name}/core.py`：定义 `{Name}Config` dataclass
3. **注册** — `strategy/strategies/{name}/registration.py`：使用 `BaseSignalGenerator` + `LiveConfig` + `ParityTestConfig` 进行注册
4. **包初始化** — `strategy/strategies/{name}/__init__.py`：仅需 docstring（自动发现，无需手动导入）

**无需修改** `test/strategy/test_all_parity.py`——它通过 `ParityTestConfig` 自动发现所有策略。
无需创建 indicator.py、live.py、configs.py 或 signal.py。通用运行器处理一切。

运行方式：`uv run python -m strategy.runner -S {name} --mesa 0`

#### 自定义版（用于需要 on_kline 覆盖的复杂策略）：

仅在策略需要逐 tick 止损检查、追踪止损、资金费率订阅或完全自定义逻辑时才添加步骤 5-6：

5. **实盘指标** — `strategy/strategies/{name}/indicator.py`：双模式包装器
6. **实盘策略** — `strategy/strategies/{name}/live.py`：自定义 `on_kline()` 覆盖

目前自定义的策略：momentum（追踪止损）、funding_rate（资金费率订阅）、grid_trading（基于 tick 的网格逻辑）。

### 设计模式

- **无循环导入**：signal_core 位于 `strategy/strategies/{name}/signal_core.py`，直接从同包内的 `.core` 导入，无需延迟导入技巧
- **配置覆盖**：信号生成器使用 `dataclasses.replace()` 来应用回测优化的参数覆盖
- **自动发现**：`strategy/strategies/__init__.py` 使用 `pkgutil.iter_modules()` 自动导入所有策略子目录中的 `registration.py`
- **K 线确认**：实盘指标使用时间戳变化检测来确认前一根 K 线已完成后再处理
- **信号映射**：实盘指标使用 `_SIGNAL_MAP` 字典将整数信号转换为交易所专用枚举值
- **双模式指标**：实盘指标启动时处于预热模式（`update_indicators_only()`），预热稳定后通过 `enable_live_mode()` 切换到实盘模式（`core.update()`），确保历史 K 线回放期间不产生虚假仓位状态
- **BaseQuantStrategy**：所有实盘交易策略的共享基类（`strategy/strategies/_base/base_strategy.py`），提供仓位跟踪、订单管理、熔断器、性能追踪、信号过滤（确认、冷却、最小持仓）、过时数据保护和模板 `on_kline()`。子类实现 `on_start()` 和 `_format_log_line()`，并可选覆盖各类钩子（`_get_signal`、`_check_stop_loss`、`_pre_signal_hook`、`_process_signal`、`_on_live_activated`）
- **GenericStrategy + GenericIndicator**：通用运行器系统（`strategy/strategies/_base/generic_strategy.py`、`generic_indicator.py`），通过 `LiveConfig` 配置消除简单到中等策略各自的 `live.py`/`indicator.py`/`configs.py` 需求。`GenericIndicator` 使用 `inspect.signature()` 自动检测信号核心方法签名并将 K 线字段映射到参数。CLI：`uv run python -m strategy.runner -S {name} --mesa 0 --exchange bitget`

### 运行一致性测试

```bash
# 运行所有指标测试（87 个：68 策略一致性 + 19 流式原语）
uv run pytest test/strategy/ -v

# 仅运行策略一致性测试（自动发现，无需手动维护）
uv run pytest test/strategy/test_all_parity.py -v

# 运行流式原语一致性测试
uv run pytest test/strategy/test_streaming_parity.py -v
```

## 统一回测框架

回测系统与交易所无关，通过统一 CLI 支持所有策略。

### 快速开始

```bash
# 统一 CLI（推荐）：
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y --full
uv run python -m strategy.backtest -S ema_crossover -X binance --heatmap
uv run python -m strategy.backtest -S bollinger_band -X okx --optimize

# 实盘交易（通用运行器 — 适用于大多数策略）：
uv run python -m strategy.runner -S ema_crossover --mesa 0
uv run python -m strategy.runner -S hurst_kalman --mesa 0 --exchange binance
uv run python -m strategy.runner --list  # 显示可用策略

# 实盘交易（自定义 — 用于复杂策略）：
uv run python -m strategy.strategies.momentum.live --mesa 3
uv run python -m strategy.strategies.funding_rate.live --mesa 0
```

### CLI 参数

| 参数 | 描述 |
|------|------|
| `-S, --strategy` | 策略名称：`hurst_kalman`、`ema_crossover`、`bollinger_band` 等 |
| `-X, --exchange` | 交易所：`bitget`、`binance`、`okx`、`bybit`、`hyperliquid` |
| `--symbol` | 交易对（默认：交易所对应的 BTC/USDT 永续合约） |
| `-p, --period` | 数据周期：`1w`、`1m`、`3m`、`6m`、`1y`、`2y`、`3y`（短周期与分析标志同用时会警告） |
| `-m, --mesa` | Mesa 配置索引（0 = 最优） |
| `--heatmap` | 运行热力图参数扫描 |
| `--heatmap-resolution` | 热力图网格分辨率（默认：15） |
| `-o, --optimize` | 网格搜索优化 |
| `-w, --walk-forward` | 前推验证 |
| `-r, --regime` | 市场状态分析 |
| `-f, --full` | 三阶段完整验证 |
| `-s, --show-results` | 显示已保存的结果 |
| `-e, --export-config` | 导出模拟交易配置 |
| `-j, --jobs` | 并行 worker 数：`1`=顺序执行（默认），`-1`=所有 CPU 核心 |
| `-L, --leverage` | 杠杆倍数（默认：1.0） |

### 架构

- `strategy/strategies/` — 所有策略代码：signal_core、注册、实盘/指标/配置（每个策略自包含）
- `strategy/strategies/_base/` — 共享基础设施（BaseSignalGenerator、BaseQuantStrategy、流式原语、测试数据）
- `strategy/backtest/` — 统一框架（运行器、CLI、注册表、交易所配置、热力图、工具函数）
- `examples/` — 交易所 API 使用示例（binance、okx、bybit、hyperliquid、bitget）

### 支持的交易所

| 交易所 | CCXT ID | Maker 费率 | Taker 费率 |
|--------|---------|-----------|-----------|
| Bitget | `bitget` | 0.02% | 0.05% |
| Binance | `binance` | 0.02% | 0.04% |
| OKX | `okx` | 0.02% | 0.05% |
| Bybit | `bybit` | 0.02% | 0.05% |
| Hyperliquid | `hyperliquid` | 0.02% | 0.05% |

### 回测方法论

回测框架应用了八项系统性修正，以确保与实盘交易表现的真实对等：

**信号延迟（1 bar 执行滞后）**
第 i 根 K 线产生的信号在第 i+1 根 K 线执行（`runner.py` 和 `heatmap.py` 中的 `_apply_signal_delay()`），防止用收盘价信号当场成交的前视偏差。

**WFO 窗口按 bar 间隔缩放**
前推验证（WFO）窗口大小通过 `_bars_per_day(interval)` 动态计算：
- 15m 策略：8640 bars 训练 / 2880 bars 测试（90天 / 30天）
- 1h 策略：2160 bars 训练 / 720 bars 测试（90天 / 30天）
- 独立 `--walk-forward` 模式在每个窗口内使用 `default_grid` 重新优化；从 `--full` 调用时使用固定参数进行稳定性检验。

**position_size_pct 传递**
资金费率策略（0.30）、网格策略（0.20）等的仓位比例正确传递至所有回测路径：单次运行、网格搜索、前推验证、热力图。

**bar 内止损**
`BaseSignalGenerator.generate()` 在每次 `core.update()` 调用后用 bar 的最低/最高价检查止损。触发时信号覆盖为 CLOSE 并重置核心状态。一致性测试在 `_run_core` 中镜像此逻辑，确保所有 87 个测试仍通过。

**资金费率数据质量**
当 `funding_rates` 为空/None 时，`use_funding_rate=False` 传递给 `CostConfig` 并打印警告。`_build_funding_rate_series` 的回退值使用 `0.0`（不建模资金成本），而非之前误导性的 `0.000014` 常量。

**Sharpe 年化自动推断**
`PerformanceAnalyzer` 和 `VectorizedBacktest._calculate_metrics()` 通过 `infer_periods_per_year()`（`nexustrader/backtest/analysis/performance.py`）从权益曲线的 DatetimeIndex 中位数推断每年周期数。无需手动配置——1h 策略自动使用约 8766，而非错误的 15m 常量 35040。

**并行扫描/优化**
`HeatmapScanner` 和 `GridSearchOptimizer` 接受 `n_jobs`（也通过 `--jobs/-j` CLI 暴露）：
- HeatmapScanner：完全并行——每个 `_run_single()` 创建新生成器（线程安全）
- GridSearchOptimizer：信号生成保持顺序（共享闭包），仅 `VectorizedBacktest.run()` 并行化
- `n_jobs=-1` 使用所有可用 CPU 核心

## Claude Code 记忆

### 工作规范
- 每次代码变更后同步更新 CLAUDE.md 和 CLAUDE_CN.md，然后 commit 并 push 到 dev 分支

### CLI 使用注意事项
- 不要在 Claude Code 中运行 nexustrader-cli moniter

### Ruff 使用
- 使用 `uvx ruff check` 检查当前目录所有文件
- 使用 `uvx ruff format` 格式化当前目录所有文件

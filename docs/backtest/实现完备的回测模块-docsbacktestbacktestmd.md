# 实现完备的回测模块

## Overview

为 NexusTrader 框架实现一个完备的回测模块，放置于 `nexustrader/backtest/` 目录下，供所有策略复用。采用混合架构：向量化回测用于快速参数优化，事件驱动回测用于最终验证（复用现有 Strategy 基类）。

## Problem Statement

当前的回测实现（`strategy/live/hurst_kalman/backtest.py`）是策略特定的，无法复用。缺乏：
- 统一的数据源抽象
- 完整的交易成本模拟（资金费率、滑点）
- 标准化的性能指标计算
- 防过拟合的验证工具（Walk-Forward、Regime Bucketing）
- 可视化报告输出

## Scope

### In Scope

- 数据层：统一数据源接口（CCXT、CSV/Parquet、PostgreSQL）
- 回测引擎：向量化 + 事件驱动双模式
- 交易成本：费率 + 滑点 + 资金费率 + 限价单成交概率
- 性能指标：基础 + 完整 + 高级指标
- 可视化：交互式 HTML 报告
- 优化工具：网格搜索 + Optuna
- 防过拟合：Walk-Forward + Regime Bucketing
- K 线周期：1m, 5m, 15m（默认）, 1h, 4h, 1d
- 交易所：MVP 仅 Bitget，预留其他接口

### Out of Scope

- 模拟交易所订单簿（流动性耗尽、深度影响）
- Paper Trading 实时模拟
- 多交易对同时回测（架构预留，但 MVP 不实现）

## User Stories

### US-1: 数据源抽象层

**Description:** 作为策略开发者，我希望通过统一接口获取历史数据，无论数据来源是 CCXT、本地文件还是数据库。

**Acceptance Criteria:**
- [ ] `DataProvider` 基类定义统一的 `fetch_klines(symbol, interval, start, end)` 接口
- [ ] `CCXTDataProvider` 实现 CCXT 数据获取，支持分页和速率限制
- [ ] `FileDataProvider` 支持 CSV 和 Parquet 格式读取
- [ ] `PostgreSQLDataProvider` 从现有 PostgreSQL 后端读取
- [ ] 所有 provider 返回相同的 `pd.DataFrame` 格式（timestamp, open, high, low, close, volume）
- [ ] 单元测试覆盖每个 provider：`uv run pytest test/backtest/test_data_provider.py`
- [ ] 类型检查通过：`uvx ruff check nexustrader/backtest/data/`

### US-2: 资金费率获取

**Description:** 作为策略开发者，我需要获取历史资金费率数据，用于永续合约回测。

**Acceptance Criteria:**
- [ ] `FundingRateProvider` 通过 CCXT 获取历史资金费率
- [ ] 支持指定时间范围获取资金费率
- [ ] 返回 `pd.DataFrame`（timestamp, funding_rate）
- [ ] 缓存机制避免重复请求
- [ ] 单元测试验证数据完整性：`uv run pytest test/backtest/test_funding_rate.py`

### US-3: 向量化回测引擎

**Description:** 作为策略开发者，我需要一个快速的向量化回测引擎用于参数优化。

**Acceptance Criteria:**
- [ ] `VectorizedBacktest` 类接受信号序列和配置
- [ ] 支持做多/做空/平仓信号
- [ ] 计算手续费（maker/taker 可配置）
- [ ] 计算资金费率（每 8 小时结算）
- [ ] 模拟滑点（固定 + ATR 动态）
- [ ] 限价单成交概率模型（简化版：价格穿越即成交）
- [ ] 返回权益曲线和交易记录
- [ ] 性能测试：2 年数据 15m K 线应在 < 1 秒内完成
- [ ] 单元测试验证计算正确性：`uv run pytest test/backtest/test_vectorized.py`

### US-4: 事件驱动回测引擎

**Description:** 作为策略开发者，我需要一个与实盘逻辑一致的事件驱动回测引擎。

**Acceptance Criteria:**
- [ ] `EventDrivenBacktest` 类接受 Strategy 实例
- [ ] 模拟 MessageBus，按时间顺序推送 Kline 事件
- [ ] 模拟 EMS，记录订单提交
- [ ] 模拟 OMS，跟踪订单状态
- [ ] 模拟账户余额和持仓
- [ ] Strategy 的 `on_kline`、`create_order` 等方法正常工作
- [ ] 与向量化引擎在相同信号下结果一致（允许 < 0.1% 误差）
- [ ] 单元测试：`uv run pytest test/backtest/test_event_driven.py`

### US-5: 性能指标计算

**Description:** 作为策略开发者，我需要全面的性能指标来评估策略。

**Acceptance Criteria:**
- [ ] `PerformanceAnalyzer` 类计算以下指标：
  - 基础：Total Return, Max Drawdown, Win Rate, Sharpe Ratio, 交易次数
  - 完整：Sortino Ratio, Calmar Ratio, Profit Factor, 换手率, 平均持仓时间
  - 高级：与 BTC 相关性（beta）, 满仓回撤, 每日/每周收益统计
- [ ] 支持年化收益率计算（考虑 24/7 市场）
- [ ] 单元测试验证每个指标的计算正确性：`uv run pytest test/backtest/test_metrics.py`
- [ ] 指标计算结果与 quantstats 对比差异 < 1%

### US-6: 交互式报告生成

**Description:** 作为策略开发者，我需要可视化报告来分析回测结果。

**Acceptance Criteria:**
- [ ] `ReportGenerator` 生成 HTML 报告
- [ ] 包含权益曲线图（plotly 交互式）
- [ ] 包含回撤曲线图
- [ ] 包含月度收益热力图
- [ ] 包含交易详情表格（可排序、筛选）
- [ ] 包含所有性能指标摘要
- [ ] 报告自包含（单个 HTML 文件，内嵌 CSS/JS）
- [ ] 单元测试验证报告生成：`uv run pytest test/backtest/test_report.py`

### US-7: 网格搜索优化

**Description:** 作为策略开发者，我需要网格搜索来分析参数敏感度。

**Acceptance Criteria:**
- [ ] `GridSearchOptimizer` 接受参数范围定义
- [ ] 并行执行回测（使用 joblib 或 concurrent.futures）
- [ ] 生成参数热力图（2D 切片）
- [ ] 输出所有组合的结果表格
- [ ] 识别"参数高原"区域
- [ ] 单元测试：`uv run pytest test/backtest/test_grid_search.py`

### US-8: Optuna 贝叶斯优化

**Description:** 作为策略开发者，我需要智能参数优化来高效搜索参数空间。

**Acceptance Criteria:**
- [ ] `OptunaOptimizer` 封装 Optuna 优化流程
- [ ] 支持自定义目标函数（默认：Sharpe * (1 - MaxDD)）
- [ ] 支持参数范围定义（连续、离散、分类）
- [ ] 支持早停和剪枝
- [ ] 输出最优参数和优化历史
- [ ] 单元测试：`uv run pytest test/backtest/test_optuna.py`

### US-9: Walk-Forward 分析

**Description:** 作为策略开发者，我需要滚动回测来验证策略的样本外表现。

**Acceptance Criteria:**
- [ ] `WalkForwardAnalyzer` 实现滚动窗口回测
- [ ] 可配置训练窗口和测试窗口大小
- [ ] 可配置滚动步长
- [ ] 输出每个测试窗口的性能指标
- [ ] 计算组合的样本外表现
- [ ] 单元测试：`uv run pytest test/backtest/test_walk_forward.py`

### US-10: 市场状态分类

**Description:** 作为策略开发者，我需要自动识别市场状态并分类统计。

**Acceptance Criteria:**
- [ ] `RegimeClassifier` 基于价格数据识别市场状态
- [ ] 识别三种状态：牛市（上涨 > 20%）、熊市（下跌 > 20%）、震荡（其他）
- [ ] 分别统计各状态下的策略表现
- [ ] 输出分状态的性能报告
- [ ] 单元测试：`uv run pytest test/backtest/test_regime.py`

### US-11: 回测结果存储

**Description:** 作为策略开发者，我需要保存和加载回测结果。

**Acceptance Criteria:**
- [ ] `BacktestResult` 数据类包含所有回测输出
- [ ] 支持序列化为 JSON 文件
- [ ] 支持从 JSON 文件反序列化
- [ ] 包含元数据：时间范围、配置参数、运行时间
- [ ] 单元测试：`uv run pytest test/backtest/test_result_storage.py`

### US-12: 策略回测脚本模板

**Description:** 作为策略开发者，我需要一个简单的脚本模板来运行回测。

**Acceptance Criteria:**
- [ ] 提供 `examples/backtest_example.py` 示例脚本
- [ ] 支持命令行参数：--start, --end, --symbol, --interval, --config
- [ ] 支持 --optimize 模式（网格搜索）
- [ ] 支持 --walk-forward 模式
- [ ] 支持 --report 生成 HTML 报告
- [ ] 文档说明使用方法
- [ ] 集成测试验证脚本可运行：`uv run python examples/backtest_example.py --help`

## Technical Design

### 目录结构

```
nexustrader/backtest/
├── __init__.py
├── data/
│   ├── __init__.py
│   ├── provider.py          # DataProvider 基类
│   ├── ccxt_provider.py     # CCXT 实现
│   ├── file_provider.py     # CSV/Parquet 实现
│   ├── postgres_provider.py # PostgreSQL 实现
│   └── funding_rate.py      # 资金费率获取
├── engine/
│   ├── __init__.py
│   ├── vectorized.py        # 向量化回测
│   ├── event_driven.py      # 事件驱动回测
│   └── cost_model.py        # 交易成本模型
├── analysis/
│   ├── __init__.py
│   ├── metrics.py           # 性能指标计算
│   ├── report.py            # HTML 报告生成
│   └── regime.py            # 市场状态分类
├── optimization/
│   ├── __init__.py
│   ├── grid_search.py       # 网格搜索
│   ├── optuna_opt.py        # Optuna 优化
│   └── walk_forward.py      # Walk-Forward 分析
└── result.py                # 回测结果数据类
```

### 数据模型

```python
@dataclass
class BacktestConfig:
    symbol: str
    interval: KlineInterval
    start_date: datetime
    end_date: datetime
    initial_capital: float = 10000.0
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage_pct: float = 0.0005
    use_funding_rate: bool = True
    exchange: str = "bitget"

@dataclass
class TradeRecord:
    timestamp: datetime
    side: str  # "buy" or "sell"
    price: float
    amount: float
    fee: float
    pnl: float
    pnl_pct: float

@dataclass
class BacktestResult:
    config: BacktestConfig
    equity_curve: pd.Series
    trades: List[TradeRecord]
    metrics: Dict[str, float]
    run_time: datetime
    duration_seconds: float
```

### 核心接口

```python
class DataProvider(ABC):
    @abstractmethod
    async def fetch_klines(
        self,
        symbol: str,
        interval: KlineInterval,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        pass

class BacktestEngine(ABC):
    @abstractmethod
    def run(self, config: BacktestConfig) -> BacktestResult:
        pass

class Optimizer(ABC):
    @abstractmethod
    def optimize(
        self,
        param_space: Dict,
        objective: Callable,
        n_trials: int,
    ) -> Dict:
        pass
```

## Requirements

### Functional Requirements

- FR-1: 数据源必须支持 CCXT、CSV/Parquet、PostgreSQL
- FR-2: 回测引擎必须支持向量化和事件驱动两种模式
- FR-3: 交易成本必须包含手续费、滑点、资金费率
- FR-4: 性能指标必须包含基础、完整、高级三个层次
- FR-5: 必须生成交互式 HTML 报告
- FR-6: 必须支持网格搜索和 Optuna 优化
- FR-7: 必须支持 Walk-Forward 分析
- FR-8: 必须支持市场状态分类统计

### Non-Functional Requirements

- NFR-1: 向量化回测 2 年 15m 数据应在 < 1 秒内完成
- NFR-2: 事件驱动回测 2 年 15m 数据应在 < 30 秒内完成
- NFR-3: 所有代码必须通过 ruff 检查
- NFR-4: 所有公共接口必须有类型注解
- NFR-5: 核心模块测试覆盖率 > 80%
- NFR-6: **每个模块实现前必须先编写测试脚本**

## Implementation Phases

### Phase 1: 数据层 (Data Layer)

- [ ] 创建 `nexustrader/backtest/` 目录结构
- [ ] 实现 `DataProvider` 基类
- [ ] 实现 `CCXTDataProvider`
- [ ] 实现 `FileDataProvider`
- [ ] 实现 `FundingRateProvider`
- [ ] **编写完整测试套件**

**Verification:**
```bash
uv run pytest test/backtest/test_data_provider.py -v
uv run pytest test/backtest/test_funding_rate.py -v
uvx ruff check nexustrader/backtest/data/
```

### Phase 2: 回测引擎 (Backtest Engine)

- [ ] 实现 `CostModel` 交易成本模型
- [ ] 实现 `VectorizedBacktest` 向量化引擎
- [ ] 实现 `EventDrivenBacktest` 事件驱动引擎
- [ ] 实现 `BacktestResult` 数据类
- [ ] **编写完整测试套件**

**Verification:**
```bash
uv run pytest test/backtest/test_vectorized.py -v
uv run pytest test/backtest/test_event_driven.py -v
uv run pytest test/backtest/test_cost_model.py -v
uvx ruff check nexustrader/backtest/engine/
```

### Phase 3: 指标与报告 (Metrics & Report)

- [ ] 实现 `PerformanceAnalyzer` 性能指标计算
- [ ] 实现 `RegimeClassifier` 市场状态分类
- [ ] 实现 `ReportGenerator` HTML 报告生成
- [ ] **编写完整测试套件**

**Verification:**
```bash
uv run pytest test/backtest/test_metrics.py -v
uv run pytest test/backtest/test_regime.py -v
uv run pytest test/backtest/test_report.py -v
uvx ruff check nexustrader/backtest/analysis/
```

### Phase 4: 优化工具 (Optimization Tools)

- [ ] 实现 `GridSearchOptimizer` 网格搜索
- [ ] 实现 `OptunaOptimizer` 贝叶斯优化
- [ ] 实现 `WalkForwardAnalyzer` 滚动分析
- [ ] 创建 `examples/backtest_example.py` 示例脚本
- [ ] **编写完整测试套件**

**Verification:**
```bash
uv run pytest test/backtest/test_grid_search.py -v
uv run pytest test/backtest/test_optuna.py -v
uv run pytest test/backtest/test_walk_forward.py -v
uv run python examples/backtest_example.py --help
uvx ruff check nexustrader/backtest/
```

## Definition of Done

This feature is complete when:
- [ ] All 12 user stories pass their acceptance criteria
- [ ] All 4 implementation phases verified
- [ ] All tests pass: `uv run pytest test/backtest/ -v`
- [ ] Types/lint check: `uvx ruff check nexustrader/backtest/`
- [ ] Performance benchmarks met (NFR-1, NFR-2)

## Ralph Loop Command

```bash
/ralph-loop:ralph-loop "Implement 实现完备的回测模块 per spec at docs/backtest/实现完备的回测模块-docsbacktestbacktestmd.md

PHASES:
1. Data Layer: DataProvider, CCXTDataProvider, FileDataProvider, FundingRateProvider - verify with pytest test/backtest/test_data*.py
2. Backtest Engine: CostModel, VectorizedBacktest, EventDrivenBacktest - verify with pytest test/backtest/test_*backtest*.py
3. Metrics & Report: PerformanceAnalyzer, RegimeClassifier, ReportGenerator - verify with pytest test/backtest/test_metrics.py test_regime.py test_report.py
4. Optimization: GridSearchOptimizer, OptunaOptimizer, WalkForwardAnalyzer - verify with pytest test/backtest/test_*opt*.py test_walk*.py

CRITICAL RULE: For each module, write tests FIRST before implementation.

VERIFICATION (run after each phase):
- uv run pytest test/backtest/ -v
- uvx ruff check nexustrader/backtest/

ESCAPE HATCH: After 20 iterations without progress:
- Document what's blocking in the spec file under 'Implementation Notes'
- List approaches attempted
- Stop and ask for human guidance

Output <promise>COMPLETE</promise> when all phases pass verification." --max-iterations 50 --completion-promise "COMPLETE"
```

## Open Questions

无

## Implementation Notes

*To be filled during implementation*

---
*Specification created: 2026-02-02*
*Interview conducted by: Claude (Lisa Plan)*

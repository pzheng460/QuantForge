# Specification: 比特币赫斯特-卡尔曼量化交易策略

*Final Specification - Created: 2026-01-30*

## Overview

在 NexusTrader 框架下为 Bitget 交易所实现比特币赫斯特-卡尔曼量化交易策略。该策略使用赫斯特指数（Hurst Exponent）判定市场状态，使用卡尔曼滤波（Kalman Filter）追踪真实价值，根据市场状态切换均值回归和趋势跟踪两种交易模式。

## Problem Statement

传统技术指标（SMA/EMA）存在滞后性，无法区分市场处于趋势还是震荡状态。本策略通过分形几何（赫斯特指数）识别市场状态，结合控制理论（卡尔曼滤波）提取真实价值信号，实现状态自适应的交易系统。

## Scope

### In Scope
- 核心算法实现（Hurst R/S 分析、Kalman 滤波器）
- NexusTrader 实盘策略（Bitget Demo 模拟盘）
- vectorbt 历史回测脚本
- 单元测试（核心算法）
- 多交易对支持（各自独立状态和仓位）
- 风控机制（熔断、止损、手续费过滤）
- PostgreSQL 交易记录存储

### Out of Scope
- Web UI 监控面板
- 多交易所支持（仅 Bitget）
- 参数自动优化器
- 移动端通知

## User Stories

### US-1: 核心算法库实现
**Description:** 作为开发者，我需要纯 numpy 实现的 Hurst 和 Kalman 算法，以便在回测和实盘间共享。

**Acceptance Criteria:**
- [ ] `calculate_hurst(prices: np.ndarray, window: int) -> float` 返回 0-1 之间的 H 值
- [ ] `KalmanFilter1D` 类支持 `update(observation)` 方法，返回滤波后的估计值
- [ ] 对于已知分布的合成数据，Hurst 计算误差 < 0.1
- [ ] 单元测试通过：`uv run pytest test/strategy/test_hurst_kalman.py`
- [ ] Lint 通过：`uvx ruff check`

### US-2: vectorbt 回测脚本
**Description:** 作为交易者，我需要在历史数据上验证策略有效性。

**Acceptance Criteria:**
- [ ] 从 Bitget API（via ccxt）获取 2023-2024 年 BTCUSDT 15m K线数据
- [ ] 使用 vectorbt 实现向量化回测
- [ ] 输出回测报告：总收益、夏普比率、最大回撤
- [ ] 脚本可运行：`uv run python strategy/live/hurst_kalman_backtest.py`

### US-3: NexusTrader 指标类实现
**Description:** 作为策略开发者，我需要 NexusTrader Indicator 类封装核心算法。

**Acceptance Criteria:**
- [ ] `HurstKalmanIndicator` 继承 `Indicator` 基类
- [ ] 支持 warmup_period=150，warmup_interval=MINUTE_15
- [ ] `handle_kline()` 更新内部状态并计算：H值、Kalman估计、Z-Score、斜率
- [ ] 提供 `get_signal()` 返回当前交易信号（BUY/SELL/HOLD/CLOSE）
- [ ] Typecheck 通过：`uvx ruff check`

### US-4: 实盘策略主体
**Description:** 作为交易者，我需要可以在 Bitget Demo 模拟盘运行的完整策略。

**Acceptance Criteria:**
- [ ] 策略继承 `Strategy` 基类
- [ ] 通过 `subscribe_klines` 订阅 15m K线
- [ ] 在 `on_kline` 中执行交易逻辑
- [ ] 支持通过配置文件指定多个交易对
- [ ] 策略可启动：`uv run python strategy/live/hurst_kalman_strategy.py`
- [ ] 日志输出当前状态：H值、Kalman价格、Z-Score、持仓

### US-5: 风控模块实现
**Description:** 作为交易者，我需要风控机制保护资金安全。

**Acceptance Criteria:**
- [ ] 单日亏损达到 3% 时触发熔断，停止当日交易
- [ ] UTC 0点自动重置熔断状态
- [ ] 硬止损：亏损达 2% 或 Z-Score > 4.0 时市价平仓
- [ ] 开仓前检查预期收益是否覆盖 0.1% 双向手续费
- [ ] 仓位计算：账户余额 × 10%

### US-6: 交易记录持久化
**Description:** 作为交易者，我需要将交易记录保存到数据库以便分析。

**Acceptance Criteria:**
- [ ] 每笔订单（开仓/平仓/止损）记录到 PostgreSQL
- [ ] 记录字段：时间、交易对、方向、数量、价格、PnL、H值、Z-Score
- [ ] 每日统计（总PnL、交易次数）可查询

## Technical Design

### 文件结构
```
strategy/live/
├── hurst_kalman/
│   ├── __init__.py
│   ├── core.py              # 纯 numpy 核心算法
│   ├── indicator.py         # NexusTrader Indicator 封装
│   ├── strategy.py          # 实盘策略主体
│   └── backtest.py          # vectorbt 回测脚本
test/strategy/
└── test_hurst_kalman.py     # 单元测试
```

### 核心算法接口

```python
# core.py
def calculate_hurst(prices: np.ndarray, window: int = 100) -> float:
    """R/S 分析计算赫斯特指数"""
    pass

class KalmanFilter1D:
    def __init__(self, R: float = 0.1, Q: float = 1e-5):
        self.R = R  # 测量噪声
        self.Q = Q  # 过程噪声
        self.x = None  # 状态估计
        self.P = 1.0   # 估计协方差

    def update(self, observation: float) -> float:
        """更新滤波器，返回估计值"""
        pass

    def get_slope(self, lookback: int = 5) -> float:
        """计算最近 lookback 个估计值的斜率"""
        pass
```

### 交易信号状态机

```
                    ┌─────────────────┐
                    │   RandomWalk    │
                    │ 0.45 ≤ H ≤ 0.55 │
                    │   禁止交易      │
                    └────────┬────────┘
                             │
         H < 0.45 ──────────┴──────────── H > 0.55
                 │                              │
    ┌────────────▼────────────┐    ┌────────────▼────────────┐
    │     MeanReversion       │    │     TrendFollowing      │
    │       H < 0.45          │    │        H > 0.55         │
    │                         │    │                         │
    │ Long: Z < -2.0          │    │ Long: Price > Kalman    │
    │ Short: Z > +2.0         │    │       && Slope > 0      │
    │ Exit: Z → 0             │    │ Short: Price < Kalman   │
    │                         │    │        && Slope < 0     │
    │ StopLoss: 2% or Z > 4   │    │ Exit: Slope 变号        │
    └─────────────────────────┘    │      or H < 0.5         │
                                   └─────────────────────────┘
```

### 配置参数

```python
@dataclass
class HurstKalmanConfig:
    # 交易对
    symbols: List[str] = field(default_factory=lambda: ["BTCUSDT-PERP.BITGET"])

    # 核心参数
    timeframe: str = "15m"
    hurst_window: int = 100
    kalman_R: float = 0.1
    kalman_Q: float = 1e-5
    zscore_window: int = 50

    # 交易阈值
    mean_reversion_threshold: float = 0.45
    trend_threshold: float = 0.55
    zscore_entry: float = 2.0
    zscore_stop: float = 4.0

    # 风控
    position_size_pct: float = 0.10  # 10%
    stop_loss_pct: float = 0.02      # 2%
    daily_loss_limit: float = 0.03   # 3%
    min_expected_profit: float = 0.001  # 0.1% 覆盖手续费
```

## Requirements

### Functional Requirements
- FR-1: 策略必须正确计算赫斯特指数（R/S 分析方法）
- FR-2: 策略必须实现一维卡尔曼滤波器
- FR-3: 策略必须根据 H 值切换均值回归/趋势跟踪模式
- FR-4: 策略必须在随机漫步状态下平仓并禁止开新仓
- FR-5: 策略必须实现 2% 硬止损和 Z-Score > 4.0 止损
- FR-6: 策略必须实现 3% 单日亏损熔断
- FR-7: 策略必须在开仓前检查预期收益是否覆盖手续费

### Non-Functional Requirements
- NFR-1: 指标计算延迟 < 10ms（单个 K 线处理）
- NFR-2: 策略支持 7×24 小时无人值守运行
- NFR-3: 日志记录所有关键状态变化和交易操作

## Implementation Phases

### Phase 1: 核心算法与测试
- [ ] 实现 `calculate_hurst()` 函数
- [ ] 实现 `KalmanFilter1D` 类
- [ ] 编写单元测试
- **Verification:** `uv run pytest test/strategy/test_hurst_kalman.py -v`

### Phase 2: vectorbt 回测
- [ ] 实现数据获取模块（ccxt + Bitget）
- [ ] 实现向量化信号生成
- [ ] 实现 vectorbt 回测主体
- [ ] 生成回测报告
- **Verification:** `uv run python strategy/live/hurst_kalman/backtest.py`

### Phase 3: NexusTrader 指标封装
- [ ] 实现 `HurstKalmanIndicator` 类
- [ ] 实现 warmup 逻辑
- [ ] 实现信号生成接口
- **Verification:** `uvx ruff check strategy/live/hurst_kalman/`

### Phase 4: 实盘策略与风控
- [ ] 实现策略主体类
- [ ] 实现风控模块（熔断、止损、手续费检查）
- [ ] 实现 PostgreSQL 交易记录
- [ ] 在 Bitget Demo 模拟盘测试运行
- **Verification:** `uv run python strategy/live/hurst_kalman/strategy.py` 启动无报错，日志正常输出

## Definition of Done

This feature is complete when:
- [ ] All acceptance criteria in user stories pass
- [ ] All implementation phases verified
- [ ] Tests pass: `uv run pytest test/strategy/test_hurst_kalman.py`
- [ ] Lint check: `uvx ruff check`
- [ ] Format check: `uvx ruff format --check`
- [ ] 策略在 Bitget Demo 模拟盘成功运行 1 小时无报错

## Ralph Loop Command

```bash
/ralph-loop "Implement Hurst-Kalman trading strategy per spec at docs/strategy/bitcoin_burst_kalman/hurst-kalman-strategy-spec.md

PHASES:
1. Core algorithms: Implement calculate_hurst() and KalmanFilter1D in core.py, write unit tests - verify with uv run pytest test/strategy/test_hurst_kalman.py
2. Backtest: Implement vectorbt backtest script with ccxt data fetching - verify with uv run python strategy/live/hurst_kalman/backtest.py
3. Indicator: Implement HurstKalmanIndicator with warmup support - verify with uvx ruff check
4. Strategy: Implement live strategy with risk management and PostgreSQL logging - verify with strategy startup test

VERIFICATION (run after each phase):
- uv run pytest test/strategy/
- uvx ruff check
- uvx ruff format --check

ESCAPE HATCH: After 20 iterations without progress:
- Document what's blocking in the spec file under 'Implementation Notes'
- List approaches attempted
- Stop and ask for human guidance

Output <promise>COMPLETE</promise> when all phases pass verification." --max-iterations 30 --completion-promise "COMPLETE"
```

## Open Questions
*None - all questions resolved during interview*

## Implementation Notes
*To be filled during implementation*

---
*Specification finalized: 2026-01-30*

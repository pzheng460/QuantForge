# 量化策略开发到上线工作流

## 概述

本文档描述从策略开发、参数优化到实盘交易的完整工作流程。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        量化策略开发到上线工作流                                │
└─────────────────────────────────────────────────────────────────────────────┘

Phase 1: 策略开发 & 初始回测
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ┌──────────────┐
    │ 策略假设形成   │ ← 技术指标、市场规律、信号逻辑
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ 初始参数设定   │ ← 基于经验/文献的合理初始值
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ 单组合回测验证 │ ← VectorizedBacktest 快速验证逻辑正确性
    └──────┬───────┘
           │
           ▼ 通过？

Phase 2: 参数优化
━━━━━━━━━━━━━━━━
    ┌──────────────┐
    │  网格搜索     │ ← GridSearchOptimizer 遍历参数空间
    │  (样本内)     │   目标: Sharpe Ratio / Calmar Ratio
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ Top N 参数组  │ ← 选取表现最好的 5-10 组参数
    └──────┬───────┘
           │
           ▼

Phase 3: 参数验证 (防止过拟合)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    ┌──────────────┐
    │ Walk-Forward │ ← WalkForwardAnalyzer
    │   滚动验证    │   训练窗口 → 测试窗口 → 滚动
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │  市场状态分析  │ ← RegimeClassifier
    │  分桶回测     │   确认各市场状态下的表现
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │  样本外测试   │ ← 用最近 20% 数据做最终验证
    │  (Hold-out)  │   绝对不能参与优化!
    └──────┬───────┘
           │
           ▼ 样本外表现 ≥ 80% 样本内表现？

Phase 4: 模拟盘交易
━━━━━━━━━━━━━━━━━━
    ┌──────────────┐
    │  Paper Trade │ ← 真实数据流 + 模拟执行
    │   (1-4 周)   │   验证: 信号触发、延迟、滑点
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │  实盘对比分析  │ ← 对比回测预期 vs 模拟实际
    │              │   偏差 < 10%?
    └──────┬───────┘
           │
           ▼ 通过？

Phase 5: 实盘交易
━━━━━━━━━━━━━━━━
    ┌──────────────┐
    │  小资金实盘   │ ← 10% 预定资金
    │   (2-4 周)   │   验证真实滑点、成交率
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │  逐步放量     │ ← 30% → 50% → 100%
    │              │   每阶段观察 1-2 周
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │  持续监控     │ ← 实时监控 + 每日/周复盘
    │  & 迭代      │   触发止损/策略失效条件时停止
    └──────────────┘
```

---

## 回测脚本使用指南

### 快速开始

回测脚本位于: `strategy/bitget/hurst_kalman/backtest.py`

```bash
# 基础回测 (默认 level 2 配置, 6个月数据)
uv run python strategy/bitget/hurst_kalman/backtest.py

# 查看已保存的回测结果
uv run python strategy/bitget/hurst_kalman/backtest.py --show-results
```

### 完整命令参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--level N` | `-l N` | 配置级别 1-5 (默认 2) |
| `--period P` | `-p P` | 回测周期: 3m, 6m, 1y, 2y (默认 6m) |
| `--all-levels` | `-a` | 测试所有 5 个配置级别 |
| `--optimize` | `-o` | 运行网格搜索参数优化 |
| `--walk-forward` | `-w` | 运行 Walk-Forward 验证 |
| `--regime` | `-r` | 运行市场状态分析 |
| `--full` | `-f` | 完整流程 (优化+验证+分析+报告) |
| `--report` | | 生成 HTML 交互式报告 |
| `--show-results` | `-s` | 显示已保存的回测结果 |
| `--export-config` | `-e` | 导出最佳配置供模拟盘使用 |

### 使用示例

```bash
# 1. 测试特定级别和周期
uv run python strategy/bitget/hurst_kalman/backtest.py --level 3 --period 1y

# 2. 测试所有级别 (1-5)
uv run python strategy/bitget/hurst_kalman/backtest.py --all-levels --period 6m

# 3. 参数优化
uv run python strategy/bitget/hurst_kalman/backtest.py --optimize --period 6m

# 4. 优化并导出配置供模拟盘使用
uv run python strategy/bitget/hurst_kalman/backtest.py --optimize --export-config

# 5. 完整回测流程 (优化 + Walk-Forward + 市场状态分析 + HTML报告)
uv run python strategy/bitget/hurst_kalman/backtest.py --full --report

# 6. 查看历史回测结果
uv run python strategy/bitget/hurst_kalman/backtest.py --show-results
```

---

## Phase 1: 策略开发 & 初始回测

### 1.1 策略假设形成

在开始编写代码之前，明确策略的核心假设：

- **市场规律**: 你认为市场存在什么可利用的规律？
- **信号逻辑**: 什么条件触发买入/卖出？
- **风险控制**: 如何限制单笔和总体风险？

### 1.2 策略分级标准 (UQSS - Universal Quant Stratification Standard)

回测脚本使用通用量化策略分级标准 (UQSS)，基于**时间视界**和**阿尔法属性**分类，而非具体指标数值：

| Level | 代号 | 中文 | 持仓周期 | Z-Score | Hurst窗口 | 说明 |
|-------|------|------|----------|---------|-----------|------|
| L1 | Macro | 宏观/结构型 | 周/月 | 3.5 | 200 | 捕捉大周期偏差，极低频 |
| L2 | Swing | 波段型 | 2-10天 | 3.0 | 100 | **推荐** 标准波段交易 |
| L3 | Intraday | 日内型 | 4-24小时 | 2.5 | 48 | 日内情绪波动 |
| L4 | Scalp | 剥头皮型 | 分钟/小时 | 2.0 | 24 | 微观结构，需低费率 |
| L5 | Sniper | 狙击/事件型 | 不定 | 4.5+ | 100 | 极端行情触发 |

**UQSS 核心理念**：不基于"指标数值"分类，而是基于"阿尔法属性（Alpha Profile）"分类。这使得分级标准可以复用到未来的趋势策略、套利策略或机器学习策略中。

### 1.3 单配置回测

```bash
# 使用推荐配置 (L2 Swing) 回测 6 个月数据
uv run python strategy/bitget/hurst_kalman/backtest.py --level 2 --period 6m
```

输出示例：
```
============================================================
BACKTEST RESULTS - L2 Swing (L2_SWING)
============================================================
Period: 2024-08-03 to 2025-02-03
Total Return: +45.23%
Max Drawdown: -12.34%
Sharpe Ratio: 1.85
Sortino Ratio: 2.31
Calmar Ratio: 3.67
Total Trades: 23
Win Rate: 56.5%
Profit Factor: 1.78
============================================================
```

---

## Phase 2: 参数优化

### 2.1 网格搜索

使用 `--optimize` 运行参数优化：

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --optimize --period 6m
```

默认优化参数网格：
- `hurst_window`: [80, 100, 120]
- `zscore_entry`: [2.0, 2.5, 3.0, 3.5]
- `mean_reversion_threshold`: [0.35, 0.40, 0.45]
- `kalman_R`: [0.1, 0.2, 0.3]

### 2.2 导出最佳配置

优化完成后，使用 `--export-config` 导出最佳参数：

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --optimize --export-config
```

这会生成 `optimized_config.py` 文件，可直接用于模拟盘：

```python
from strategy.bitget.hurst_kalman.optimized_config import OPTIMIZED_CONFIG

# 在策略中使用
hk_config, filter_config = OPTIMIZED_CONFIG.get_configs()
```

### 2.3 参数选择标准

| 指标 | 最低标准 | 理想标准 |
|------|---------|---------|
| Sharpe Ratio | > 1.0 | > 1.5 |
| Max Drawdown | < 30% | < 20% |
| Win Rate | > 40% | > 50% |
| Profit Factor | > 1.2 | > 1.5 |
| Total Trades | > 20 | > 50 |

---

## Phase 3: 参数验证

### 3.1 Walk-Forward 分析

防止过拟合的关键步骤：

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --walk-forward --period 6m
```

输出示例：
```
============================================================
WALK-FORWARD VALIDATION
============================================================
Windows: 12
Avg Train Return: 8.52%
Avg Test Return: 2.34%
Robustness Ratio: 0.78
Positive Test Windows: 9/12
Total Test Return: 28.08%
============================================================
```

### 3.2 市场状态分析

确认策略在不同市场状态下的表现：

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --regime --period 6m
```

输出示例：
```
============================================================
MARKET REGIME ANALYSIS
============================================================

Regime Distribution:
  trending_up_pct: 32.5%
  trending_down_pct: 18.2%
  ranging_pct: 41.8%
  high_volatility_pct: 7.5%

Performance by Regime:
  trending_up: +12.34% return (2880 bars)
  trending_down: -3.21% return (1620 bars)
  ranging: +28.56% return (3712 bars)
  high_volatility: +5.67% return (668 bars)
============================================================
```

### 3.3 完整验证流程

一次性运行所有验证：

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --full --report
```

### 3.4 验证检查点

| 检查项 | 通过标准 |
|-------|---------|
| Walk-Forward 鲁棒性比率 | > 0.7 |
| 正收益窗口比例 | > 60% |
| 样本外收益衰减 | < 30% |
| 各市场状态正收益 | ≥ 3/4 状态 |

---

## Phase 4: 模拟盘交易

### 4.1 导出配置并启动模拟盘

```bash
# 1. 导出最佳配置
uv run python strategy/bitget/hurst_kalman/backtest.py --optimize --export-config

# 2. 修改策略文件使用导出的配置
# 在 strategy.py 中:
from strategy.bitget.hurst_kalman.optimized_config import OPTIMIZED_CONFIG
```

### 4.2 使用 Testnet 配置

```python
from nexustrader.exchange import BitgetAccountType

config = Config(
    basic_config={
        ExchangeType.BITGET: BasicConfig(
            api_key=settings.BITGET.DEMO.API_KEY,
            secret=settings.BITGET.DEMO.SECRET,
            passphrase=settings.BITGET.DEMO.PASSPHRASE,
            testnet=True,  # 启用测试网
        )
    },
)
```

### 4.3 模拟盘检查项

运行 1-4 周，检查：

- [ ] 信号触发频率与回测一致
- [ ] 实际滑点在预期范围内
- [ ] 订单成交率 > 95%
- [ ] 无异常订单/挂单

### 4.4 偏差分析

对比回测与模拟盘结果：

| 指标 | 回测预期 | 模拟实际 | 偏差 |
|------|---------|---------|------|
| 信号数量 | X | Y | Y/X |
| 平均滑点 | 0.05% | ? | ? |
| 胜率 | 55% | ? | ? |
| 收益率 | 10% | ? | ? |

**通过标准**: 所有偏差 < 20%

---

## Phase 5: 实盘交易

### 5.1 小资金测试

- 使用预定资金的 10%
- 运行 2-4 周
- 确认真实滑点、手续费

### 5.2 逐步放量

```
Week 1-2:  10% 资金
Week 3-4:  30% 资金 (如果表现达标)
Week 5-6:  50% 资金
Week 7+:   100% 资金
```

### 5.3 停止条件

触发以下任一条件时停止交易：

- [ ] 单日亏损 > 3%
- [ ] 连续 3 天亏损
- [ ] 周亏损 > 10%
- [ ] 最大回撤 > 预期的 1.5 倍
- [ ] 信号偏差 > 30%

### 5.4 持续监控

每日检查：
- 当日 PnL
- 持仓状态
- 订单执行情况

每周复盘：
- 周收益率 vs 回测预期
- 实际 vs 预期滑点
- 市场状态分布

---

## 结果持久化

### 保存位置

- **回测结果**: `strategy/bitget/hurst_kalman/backtest_results.json`
- **HTML 报告**: `strategy/bitget/hurst_kalman/backtest_report.html`
- **导出配置**: `strategy/bitget/hurst_kalman/optimized_config.py`

### 查看历史结果

```bash
uv run python strategy/bitget/hurst_kalman/backtest.py --show-results
```

输出示例：
```
===============================================================================================
BACKTEST RESULTS SUMMARY (UQSS Tiering)
===============================================================================================

6 MONTHS
-----------------------------------------------------------------------------------------------
Level    UQSS Tier              Return   Sharpe     MaxDD    WinRate   Trades
-----------------------------------------------------------------------------------------------
L1      Macro                   +42.3%     1.85     -5.2%      66.7%        3
L2      Swing         [REC]     +45.2%     1.92    -12.3%      56.5%       23 **
L3      Intraday                +38.7%     1.45    -18.5%      48.3%       45
L4      Scalp                   +22.1%     0.89    -28.4%      42.1%       89
L5      Sniper                  +41.8%     1.78    -14.2%      52.4%       31

OPTIMIZATION RESULTS
-----------------------------------------------------------------------------------------------
Period   Z-Score   Hurst     Return   Sharpe     MaxDD   Trades
-----------------------------------------------------------------------------------------------
6m          2.5     0.40     +52.3%     2.15    -10.8%       28

===============================================================================================
```

---

## 配置快速切换 (模拟盘)

### 方法 1: 使用导出的配置

```python
# 导出配置后
from strategy.bitget.hurst_kalman.optimized_config import OPTIMIZED_CONFIG

hk_config, filter_config = OPTIMIZED_CONFIG.get_configs()
```

### 方法 2: 使用 UQSS 级别

```python
from strategy.bitget.hurst_kalman.configs import get_config

# 切换到不同 UQSS 级别
strategy_config = get_config(level=2)  # L1-L5
hk_config, filter_config = strategy_config.get_configs()

# 可用级别:
# L1 = Macro (宏观/结构型)
# L2 = Swing (波段型) [推荐]
# L3 = Intraday (日内型)
# L4 = Scalp (剥头皮型)
# L5 = Sniper (狙击/事件型)
```

### 方法 3: 按名称获取

```python
from strategy.bitget.hurst_kalman.configs import get_config_by_name

strategy_config = get_config_by_name("swing")
# 可用名称: macro, swing, intraday, scalp, sniper
```

### 方法 4: 使用 StrategyLevel 枚举

```python
from nexustrader.strategy_config import StrategyLevel
from strategy.bitget.hurst_kalman.configs import get_config_by_level

strategy_config = get_config_by_level(StrategyLevel.L2_SWING)
hk_config, filter_config = strategy_config.get_configs()
```

---

## UQSS 通用量化策略分级标准

### 设计理念

UQSS (Universal Quant Stratification Standard) 的核心逻辑是：**不基于"指标数值"，而是基于"阿尔法属性（Alpha Profile）"**。

这意味着分级标准不依赖于具体的指标名称（如 Z-Score 或 RSI），而是基于所有策略共有的核心维度：
- **时间视界 (Time Horizon)**: 持仓周期长度
- **信号特异性 (Specificity)**: 入场条件的严格程度
- **风险特征 (Risk Profile)**: 预期胜率和盈亏比

### 如何将 UQSS 映射到新策略

#### 案例 1: Hurst-Kalman (均值回归)

| Level | 参数映射 | 适用场景 |
|-------|----------|----------|
| L1 | `hurst_window=200`, `zscore_entry=3.5` | 只在历史大底或大顶介入 |
| L2 | `hurst_window=100`, `zscore_entry=3.0` | 做几天的反弹或回调 |
| L3 | `hurst_window=48`, `zscore_entry=2.5` | 日内震荡区间高抛低吸 |
| L4 | `hurst_window=24`, `zscore_entry=2.0` | 分钟级波动刷单 |
| L5 | `zscore_entry=4.5+` | 挂在"天地针"位置的限价单 |

#### 案例 2: SuperTrend (趋势追踪)

| Level | 参数映射 | 适用场景 |
|-------|----------|----------|
| L1 | `ATR=100`, `Factor=5` | 比特币 4 年周期大牛市 |
| L2 | `ATR=20`, `Factor=3` | 持续几周的单边行情 |
| L3 | `ATR=10`, `Factor=2` | 日内追涨杀跌 |
| L4 | `ATR=5`, `Factor=1.5` | 分钟线剥头皮 |
| L5 | ATH 突破 | 突破历史新高无条件追入 |

### 代码实现

```python
from nexustrader.strategy_config import StrategyLevel, UniversalConfig

# 定义通用配置
config = UniversalConfig(
    level=StrategyLevel.L2_SWING,
    name="My Strategy Swing",
    description="Multi-day swing trading",
    timeframe="4h",
    risk_per_trade=0.02,
    max_holding_bars=100,
    params={
        # 策略特定参数
        "indicator_param_1": value1,
        "indicator_param_2": value2,
    }
)
```

详见 `nexustrader/strategy_config/config_schema.py`。

---

## 参考

- [NexusTrader 回测模块文档](./backtest.md)
- [Hurst-Kalman 策略规格](../strategy/bitcoin_burst_kalman/hurst-kalman-strategy-spec.md)
- [UQSS 配置模式](../../nexustrader/strategy_config/config_schema.py)

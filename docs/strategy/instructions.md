# Hurst-Kalman 量化交易策略

## 概述

Hurst-Kalman 策略是一个基于统计套利的量化交易策略，结合了 Hurst 指数和 Kalman 滤波器来识别市场状态并生成交易信号。

### 核心原理

1. **Hurst 指数** - 用于识别市场状态
   - H < 0.40: 均值回归 (Mean-Reverting) - 适合反向交易
   - H = 0.50: 随机游走 (Random Walk) - 不交易
   - H > 0.60: 趋势跟踪 (Trending) - 适合趋势交易

2. **Kalman 滤波器** - 估计"真实"价格
   - 提供零滞后的价格估计
   - 计算价格偏离度 (Z-Score)

3. **Z-Score** - 生成交易信号
   - Z > 阈值: 做空信号 (价格过高)
   - Z < -阈值: 做多信号 (价格过低)

---

## 文件结构

```
strategy/live/hurst_kalman/
├── configs.py              # 配置定义 (1-5级别)
├── backtest.py             # 统一回测脚本 (支持参数控制)
├── backtest_results.json   # 回测结果数据
├── performance.py          # 实时性能跟踪
├── live_performance.json   # 实盘性能数据
├── core.py                 # 核心算法实现
├── indicator.py            # NexusTrader 指标封装
├── strategy.py             # 实盘策略 (修改 SELECTED_CONFIG 选择配置)
└── hurst_kalman.log        # 策略运行日志
```

---

## 配置系统

### 配置级别 (1-5)

策略提供 5 个预设配置级别：

| 级别 | 名称 | 风险 | Z-Score阈值 | Hurst阈值 | 6个月收益 | 推荐 |
|------|------|------|-------------|-----------|-----------|------|
| 1 | Ultra Conservative | 极低 | 4.0 | 0.35 | +42.3% | 长期稳定 |
| 2 | Conservative | 低 | 3.0 | 0.40 | +135.5% | **长期推荐** |
| 3 | Moderate | 中 | 2.5 | 0.42 | +89.2% | 不推荐 |
| 4 | Aggressive | 高 | 2.0 | 0.45 | +45.7% | 不推荐 |
| **5** | **Short-Term Balanced** | **低-中** | **2.8** | **0.40** | **+135.3%** | **短期推荐** |

**说明：**
- Level 2 和 Level 5 是推荐配置
- Level 3/4 长期回测亏损，不推荐实盘使用
- Level 5 专为短期交易优化，交易频率比 Level 2 更高

### 查看所有配置

```bash
uv run python -m strategy.live.hurst_kalman.configs
```

输出示例：
```
======================================================================
HURST-KALMAN CONFIGURATIONS (1=Conservative -> 5=Short-Term)
======================================================================

[1] ULTRA CONSERVATIVE
    Risk: very_low
    2-year: +12.8% return, 57.1% win rate, -15.3% max DD

[2] CONSERVATIVE [RECOMMENDED FOR LONG-TERM]
    Risk: low
    2-year: +34.4% return, 55.0% win rate, -34.4% max DD

[3] MODERATE
    Risk: medium
    2-year: -28.6% return, 40.8% win rate, -52.3% max DD

[4] AGGRESSIVE
    Risk: high
    2-year: -47.3% return, 33.8% win rate, -65.2% max DD

[5] SHORT-TERM BALANCED [RECOMMENDED FOR SHORT-TERM]
    Risk: low_medium
    6-month: +135.3% return, 44.4% win rate, -0.1% max DD

======================================================================
Usage: SELECTED_CONFIG = 2  # Select level 1-5
======================================================================
```

### 在策略中选择配置

**文件位置：** `strategy/live/hurst_kalman/strategy.py` 第 541 行

**修改方法：** 将 `SELECTED_CONFIG` 的值改为 1-5：

```python
SELECTED_CONFIG = 1  # Ultra Conservative (极保守)
SELECTED_CONFIG = 2  # Conservative (保守，长期推荐) ← 默认
SELECTED_CONFIG = 3  # Moderate (中等，不推荐)
SELECTED_CONFIG = 4  # Aggressive (激进，不推荐)
SELECTED_CONFIG = 5  # Short-Term Balanced (短期平衡，短期推荐)
```

**完整代码上下文：**

```python
# =============================================================================
# CONFIGURATION SELECTION (1=Conservative -> 5=Short-Term)
# =============================================================================
# Level 1: Ultra Conservative - Very low risk, few trades
# Level 2: Conservative      - Low risk, recommended for long-term [DEFAULT]
# Level 3: Moderate          - Medium risk (NOT recommended)
# Level 4: Aggressive        - High risk (NOT recommended)
# Level 5: Short-Term        - Low-medium risk, optimized for short-term gains
# =============================================================================

SELECTED_CONFIG = 2  # ← 修改这个数字 (1-5)

# Load configuration from configs.py
selected = get_config(SELECTED_CONFIG)
strategy_config, filter_config = selected.get_configs()
```

---

## 回测系统

### 统一回测脚本

所有回测功能已整合到单一脚本 `backtest.py`：

```bash
# 回测所有配置 (Level 1-5) 和时间段 (6m, 1y, 2y)
uv run python -m strategy.live.hurst_kalman.backtest

# 只测试特定级别
uv run python -m strategy.live.hurst_kalman.backtest --level 2

# 只测试特定时间段
uv run python -m strategy.live.hurst_kalman.backtest --period 6m

# 组合使用
uv run python -m strategy.live.hurst_kalman.backtest --level 5 --period 3m

# 参数优化（寻找最佳短期配置）
uv run python -m strategy.live.hurst_kalman.backtest --optimize

# 查看已保存的结果
uv run python -m strategy.live.hurst_kalman.backtest --show-results
```

### 时间段选项

| 参数 | 说明 |
|------|------|
| `3m` | 最近 3 个月 |
| `6m` | 最近 6 个月 |
| `1y` | 最近 1 年 |
| `2y` | 最近 2 年 |

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--level N` / `-l N` | 测试特定级别 (1-5) |
| `--period X` / `-p X` | 测试特定时间段 (3m/6m/1y/2y) |
| `--optimize` / `-o` | 运行参数优化 |
| `--show-results` / `-s` | 仅显示已保存结果 |

### 回测结果存储

回测结果自动保存到 `backtest_results.json`：

```json
{
  "2_2_years": {
    "config_level": 2,
    "config_name": "Conservative",
    "period": "2_years",
    "start_date": "2023-01-01",
    "end_date": "2025-01-01",
    "backtest_run_time": "2025-01-30 08:30:00",
    "total_return_pct": 34.4,
    "win_rate_pct": 55.0,
    "max_drawdown_pct": -34.4,
    "total_trades": 20,
    "sharpe_ratio": 1.25,
    ...
  }
}
```

---

## 运行策略

### 模拟盘测试

```bash
# 运行策略 (配置由 SELECTED_CONFIG 决定)
uv run python -m strategy.live.hurst_kalman.strategy
```

**切换配置：** 修改 `strategy.py` 中的 `SELECTED_CONFIG = 1/2/3/4/5`

### 日志文件

策略运行日志保存在策略目录下：`hurst_kalman.log`

### 日志格式示例

```
2026-01-30T08:17:43.522584000Z [INFO] BTCUSDT-PERP.BITGET | Bar=150 | H=0.569 | Z=-0.84 | State=random_walk | Signal=close | Pos=FLAT | Hold=0 | CD=0
```

字段说明：
- `Bar` - K线编号
- `[PERF]` - 性能统计日志（每小时输出一次）

### 实时性能监控

策略运行时会自动跟踪收益率、胜率、最大回撤等指标，数据保存在 `live_performance.json`。

**查看实时性能：**
```bash
uv run python -m strategy.live.hurst_kalman.performance
```

**输出示例：**
```
============================================================
LIVE PERFORMANCE STATS
============================================================
Session Start: 2026-01-30T19:00:00
Config: Level 2 (Conservative)
------------------------------------------------------------
Initial Balance:  50,000.00 USDT
Current Balance:  51,234.56 USDT
Total P&L:        +1,234.56 USDT (+2.47%)
------------------------------------------------------------
Total Trades:     5
Win Rate:         60.0% (3W / 2L)
Avg Win:          +2.50%
Avg Loss:         -1.20%
Profit Factor:    2.08
------------------------------------------------------------
Max Drawdown:     1.50%
Current Drawdown: 0.30%
============================================================
```

**性能数据文件：** `strategy/live/hurst_kalman/live_performance.json`

---

## 日志字段说明
- `H` - Hurst 指数
- `Z` - Z-Score
- `State` - 市场状态 (mean_reverting/random_walk/trending)
- `Signal` - 信号 (buy/sell/hold/close)
- `Pos` - 当前仓位
- `Hold` - 持仓时间 (bars)
- `CD` - 冷却时间剩余 (bars)

---

## 完整工作流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        开发/优化阶段                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 修改配置参数 (configs.py)                                    │
│         ↓                                                       │
│  2. 运行回测 (backtest_all.py)                                   │
│         ↓                                                       │
│  3. 结果自动保存 → backtest_results.json                         │
│         ↓                                                       │
│  4. 查看结果 (results.py)                                        │
│         ↓                                                       │
│  5. 选择最佳配置 → SELECTED_CONFIG = N                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                        模拟盘测试                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 运行策略 (strategy.py)                                       │
│         ↓                                                       │
│  2. 监控日志 (hurst_kalman.log)                                  │
│         ↓                                                       │
│  3. 验证信号和交易                                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                         实盘交易                                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 修改账户类型: UTA_DEMO → UTA                                 │
│  2. 修改 testnet: True → False                                  │
│  3. 配置实盘 API 密钥                                            │
│  4. 运行策略                                                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 配置参数详解

### 策略参数 (HurstKalmanConfig)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `hurst_window` | Hurst 计算窗口 | 100 |
| `zscore_window` | Z-Score 计算窗口 | 60 |
| `zscore_entry` | 开仓 Z-Score 阈值 | 3.0 |
| `mean_reversion_threshold` | 均值回归 Hurst 阈值 | 0.40 |
| `trend_threshold` | 趋势跟踪 Hurst 阈值 | 0.60 |
| `kalman_R` | Kalman 观测噪声 | 0.2 |
| `kalman_Q` | Kalman 过程噪声 | 5e-05 |
| `position_size_pct` | 仓位大小 (账户百分比) | 0.10 |
| `stop_loss_pct` | 止损百分比 | 0.03 |
| `daily_loss_limit` | 日亏损限制 | 0.03 |

### 过滤参数 (TradeFilterConfig)

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `min_holding_bars` | 最小持仓时间 (bars) | 8 (2小时) |
| `cooldown_bars` | 平仓后冷却时间 (bars) | 4 (1小时) |
| `signal_confirmation` | 信号确认次数 | 1 |
| `only_mean_reversion` | 仅均值回归交易 | True |

---

## 账户类型

### Bitget 账户类型

| 类型 | 说明 | 用途 |
|------|------|------|
| `UTA_DEMO` | 统一交易账户模拟盘 | 测试 |
| `UTA` | 统一交易账户实盘 | 实盘交易 |
| `FUTURE_DEMO` | 合约模拟盘 (传统) | 测试 |
| `FUTURE` | 合约实盘 (传统) | 实盘交易 |

### 切换到实盘

编辑 `strategy.py`：

```python
# 修改账户类型
account_type=BitgetAccountType.UTA  # UTA_DEMO → UTA

# 修改 testnet 标志
testnet=False  # True → False
```

---

## 最佳实践

### 1. 回测验证

- 始终使用 2 年以上的数据进行回测
- 注意过拟合风险，短期高收益可能不可持续
- 比较不同配置在相同时间段的表现

### 2. 模拟盘测试

- 在模拟盘运行至少 1-2 周
- 验证信号与回测结果一致
- 确认订单执行正常

### 3. 风险管理

- 长期交易建议使用 Level 2 (Conservative) 配置
- 短期交易可使用 Level 5 (Short-Term Balanced) 配置
- **不推荐使用 Level 3/4**（长期亏损）
- 设置合理的仓位大小 (建议 ≤ 10%)
- 启用日亏损限制 (circuit breaker)

### 4. 监控

- 定期检查日志文件
- 关注 Hurst 指数变化
- 监控实际收益与回测差异

---

## 常见问题

### Q: 为什么策略一直显示 FLAT？

A: 策略使用严格的入场条件：
- 需要 Hurst < 0.40 (均值回归状态)
- 需要 |Z-Score| > 3.0 (极端偏离)
- 两个条件同时满足才会开仓

### Q: 如何增加交易频率？

A: 使用 Level 5 (Short-Term Balanced) 配置，它在保持盈利的同时提供更多交易机会。**不推荐使用 Level 3/4**，因为长期回测显示亏损。

### Q: 回测结果与实盘差异大怎么办？

A: 检查以下因素：
- 滑点和手续费
- 数据质量
- 市场流动性
- 订单执行延迟

---

## 更新日志

### 2026-01-30
- 新增 Level 5: Short-Term Balanced 配置（短期优化）
- 更新配置系统支持 1-5 级别
- 添加短期回测脚本 (backtest_short_term.py)
- 修复 Bitget OMS 的 posSide 参数问题（one-way mode）

### 2025-01-30
- 创建配置系统 (configs.py)
- 添加回测结果自动保存
- 支持数字级别选择 (1-4，后更新为1-5)
- 添加综合回测脚本 (backtest_all.py)

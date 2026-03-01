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

## 统一回测 CLI

所有策略共享同一个回测入口 `strategy/backtest`，无需针对每个策略单独维护脚本。

### 快速开始

```bash
# 基础单次回测（1 年数据，使用 Mesa #0 最优参数）
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y

# 完整三阶段验证（推荐用于生产验证）
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y --full

# 查看已保存的回测结果
uv run python -m strategy.backtest -S hurst_kalman -X bitget --show-results
```

### 时间周期选项

| 周期 | 天数 | 说明 |
|------|------|------|
| `1w` | 7天 | 快速验证 |
| `1m` | 30天 | 1个月 |
| `3m` | 90天 | 3个月 - 最短验证周期 |
| `6m` | 180天 | 6个月 - 中等验证周期 |
| `1y` | 365天 | **1年 (默认)** - 推荐验证周期 |
| `2y` | 730天 | 2年 - 完整市场周期验证 |
| `3y` | 1095天 | 3年 - 长期验证 |

### 完整命令参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `-S, --strategy` | | 策略名称: `hurst_kalman`, `ema_crossover`, `bollinger_band` 等 |
| `-X, --exchange` | | 交易所: `bitget`, `binance`, `okx`, `bybit`, `hyperliquid` |
| `--symbol` | | 交易对 (默认: 各交易所 BTC/USDT 永续合约) |
| `-p, --period` | | 数据周期: `1w`, `1m`, `3m`, `6m`, `1y`, `2y`, `3y` |
| `-m, --mesa` | | Mesa 配置索引 (0 = 最优) |
| `--heatmap` | | 运行热力图参数扫描 |
| `--heatmap-resolution` | | 热力图网格分辨率 (默认: 15) |
| `-o, --optimize` | | 网格搜索优化 |
| `-w, --walk-forward` | | 滚动验证 |
| `-r, --regime` | | 市场状态分析 |
| `-f, --full` | | 三阶段完整验证（优化 + 滚动验证 + 样本外测试）|
| `-s, --show-results` | | 显示已保存的回测结果 |
| `-e, --export-config` | | 导出配置用于实盘 |
| `-j, --jobs` | | 并行 worker 数: `1`=串行 (默认), `-1`=全部 CPU 核心 |
| `-L, --leverage` | | 杠杆倍数 (默认: 1.0) |

### 完整回测验证模式 (--full)

完整验证模式包含以下步骤：

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    完整回测验证模式 (Comprehensive Validation)                  │
└─────────────────────────────────────────────────────────────────────────────┘

阶段 1: 样本内优化 (80% 数据)
━━━━━━━━━━━━━━━━━━━━━━━━
  - 热力图扫描找出高 Sharpe 参数区域（mesa）
  - 固定最优参数后网格搜索细化
  - 通过标准: Sharpe >= 1.0

阶段 2: 滚动验证 (Walk-Forward)
━━━━━━━━━━━━━━━━━━━━━━━━
  - 1h 策略: 训练窗口 90 天，测试窗口 30 天
  - 使用固定的最优参数（非重新优化）
  - 通过标准: 鲁棒性 >= 0.5, 正收益窗口 >= 50%

阶段 3: 样本外测试 (20% 数据)
━━━━━━━━━━━━━━━━━━━━━━━━
  - 最终验证 + 市场状态分析
  - 通过标准: 性能衰减 <= 50%, Sharpe >= 0.5
```

### 使用示例

```bash
# 1. 完整验证模式（推荐）
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y --full

# 2. 热力图参数扫描（找 mesa 区域）
uv run python -m strategy.backtest -S bollinger_band -X binance --heatmap

# 3. 网格搜索优化
uv run python -m strategy.backtest -S ema_crossover -X okx --optimize

# 4. 仅滚动验证（用已知参数）
uv run python -m strategy.backtest -S hurst_kalman -X bitget --walk-forward

# 5. 市场状态分析
uv run python -m strategy.backtest -S momentum -X bitget --regime

# 6. 导出最优配置用于实盘
uv run python -m strategy.backtest -S hurst_kalman -X bitget --export-config

# 7. 并行加速（使用全部 CPU 核心）
uv run python -m strategy.backtest -S hurst_kalman -X bitget --heatmap -j -1
```

---

## Phase 1: 策略开发 & 初始回测

### 1.1 策略假设形成

在开始编写代码之前，明确策略的核心假设：

- **市场规律**: 你认为市场存在什么可利用的规律？
- **信号逻辑**: 什么条件触发买入/卖出？
- **风险控制**: 如何限制单笔和总体风险？

### 1.2 单配置回测

```bash
# 使用 Mesa #0（最优参数）回测 1 年数据
uv run python -m strategy.backtest -S hurst_kalman -X bitget -p 1y
```

---

## Phase 2: 参数优化

### 2.1 热力图扫描

```bash
# 生成热力图，识别高 Sharpe 参数区域
uv run python -m strategy.backtest -S bollinger_band -X bitget --heatmap
```

热力图自动检测稳定的高 Sharpe 区域（mesa），结果保存到
`strategy/results/{strategy}/{exchange}/heatmap_results.json`。

### 2.2 网格搜索

```bash
uv run python -m strategy.backtest -S hurst_kalman -X bitget --optimize -p 1y
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
uv run python -m strategy.backtest -S hurst_kalman -X bitget --walk-forward -p 1y
```

### 3.2 市场状态分析

确认策略在不同市场状态下的表现：

```bash
uv run python -m strategy.backtest -S hurst_kalman -X bitget --regime -p 1y
```

### 3.3 完整验证流程

一次性运行所有验证：

```bash
uv run python -m strategy.backtest -S hurst_kalman -X bitget --full -p 1y
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

### 4.1 导出配置并启动实盘

```bash
# 导出最佳配置
uv run python -m strategy.backtest -S hurst_kalman -X bitget --export-config

# 使用通用 runner 启动模拟盘（大多数策略）
uv run python -m strategy.runner -S hurst_kalman --mesa 0 --exchange bitget

# 查看可用策略列表
uv run python -m strategy.runner --list
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

- **回测结果**: `strategy/results/{strategy}/{exchange}/`
- **热力图结果**: `strategy/results/{strategy}/{exchange}/heatmap_results.json`

### 查看历史结果

```bash
uv run python -m strategy.backtest -S hurst_kalman -X bitget --show-results
```

---

## 参考

- [CLAUDE.md — 回测框架架构](../../CLAUDE.md)
- [strategy/backtest/](../../strategy/backtest/) — 统一回测框架源码
- [strategy/runner.py](../../strategy/runner.py) — 通用实盘 runner

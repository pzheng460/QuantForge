# Hurst-Kalman 策略完整工作流

## 目录

1. [系统概览](#1-系统概览)
2. [核心算法原理](#2-核心算法原理)
3. [回测系统详解](#3-回测系统详解)
4. [如何选择策略参数](#4-如何选择策略参数)
5. [从回测到实盘的完整流程](#5-从回测到实盘的完整流程)
6. [命令参考](#6-命令参考)
7. [文件说明](#7-文件说明)

---

## 1. 系统概览

### 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     参数发现 (Parameter Discovery)               │
│                                                                 │
│  backtest.py --heatmap                                          │
│      │                                                          │
│      ├─→ HeatmapScanner    扫描 zscore_entry × hurst_window 网格│
│      ├─→ MesaDetector      BFS洪水填充检测盈利高原              │
│      ├─→ FrequencyAnalyzer  按交易频率分桶                      │
│      └─→ ConfigExporter     导出Mesa配置                        │
│                                                                 │
│  产出:                                                          │
│    heatmap_results.json   ← 完整扫描数据 + Mesa配置             │
│    heatmap_report.html    ← 交互式热力图报告                    │
│    optimized_config.py    ← 可直接import的Python配置            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     参数验证 (Parameter Validation)              │
│                                                                 │
│  backtest.py --full                                             │
│      │                                                          │
│      ├─→ Stage 1: 样本内优化 (Grid Search, 80%数据)            │
│      ├─→ Stage 2: 滚动验证 (Walk-Forward, 30天训练/7天测试)    │
│      └─→ Stage 3: 样本外测试 (Holdout 20% + 市场状态分析)      │
│                                                                 │
│  通过标准:                                                      │
│    Stage 1: Sharpe >= 1.0                                       │
│    Stage 2: 鲁棒性 >= 0.5, 正收益窗口 >= 50%                   │
│    Stage 3: 性能衰减 <= 50%, Sharpe >= 0.5                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     模拟交易 (Paper Trading)                     │
│                                                                 │
│  python -m strategy.live.hurst_kalman.strategy --mesa 0       │
│      │                                                          │
│      ├─→ get_config(0) 从 heatmap_results.json 加载最优配置     │
│      ├─→ HurstKalmanStrategy 实时策略运行                       │
│      └─→ PerformanceTracker 实时绩效跟踪                        │
│                                                                 │
│  监控:                                                          │
│    paper_validate.py      ← 多阶段模拟验证报告                  │
│    live_performance.json  ← 实时绩效数据                        │
└─────────────────────────────────────────────────────────────────┘
```

### 数据流

```
Bitget BTC/USDT 15分钟K线
    → Hurst指数计算 (R/S分析) → 市场状态分类
    → Kalman滤波 → 真实价格估计
    → Z-Score → 偏离度信号
    → 交易过滤 (持仓期/冷却期/确认)
    → 信号输出 (BUY/SELL/CLOSE/HOLD)
```

---

## 2. 核心算法原理

### 2.1 Hurst 指数 (市场状态识别)

用 R/S (Rescaled Range) 分析计算，窗口大小 = `hurst_window`。

| Hurst值 | 市场状态 | 含义 |
|---------|---------|------|
| H < `mean_reversion_threshold` (默认0.4) | 均值回复 (mean_reverting) | 价格倾向回归均值，适合反向交易 |
| `mean_reversion_threshold` <= H <= `trend_threshold` | 随机游走 (random_walk) | 无明确趋势，不交易 |
| H > `trend_threshold` (默认0.6) | 趋势 (trending) | 价格有持续趋势 |

### 2.2 Kalman 滤波 (真实价格估计)

一维 Kalman 滤波器平滑价格，关键参数：
- `kalman_R` (测量噪声): 越大 → 越信任模型预测，滤波越平滑
- `kalman_Q` (过程噪声): 越大 → 状态变化越快，跟踪越灵敏

### 2.3 Z-Score (信号生成)

```
Z-Score = (当前价格 - Kalman估计价) / rolling_std(偏差, zscore_window)
```

**均值回复模式下的信号逻辑:**
- Z-Score < -`zscore_entry` → **BUY** (价格过低，预期回升)
- Z-Score > +`zscore_entry` → **SELL** (价格过高，预期回落)
- |Z-Score| < 0.5 → **CLOSE** (回归均值，平仓)

### 2.4 交易过滤

| 过滤器 | 参数 | 说明 |
|--------|------|------|
| 最小持仓期 | `min_holding_bars` | 开仓后至少持有N根K线才允许平仓 (防过度交易) |
| 冷却期 | `cooldown_bars` | 平仓后等待N根K线才允许开新仓 |
| 信号确认 | `signal_confirmation` | 连续N次相同信号才执行 (默认1=不需要) |
| 仅均值回复 | `only_mean_reversion` | 只在H<阈值时交易 (默认True，最稳健) |

### 2.5 风险管理

| 机制 | 参数 | 说明 |
|------|------|------|
| 止损 | `stop_loss_pct` | 亏损超过此比例立即平仓 (忽略最小持仓期) |
| Z-Score止损 | `zscore_stop` | Z-Score极端值触发模型失效止损 |
| 日损限制 | `daily_loss_limit` | 日内亏损超过此比例触发熔断，关闭所有仓位 |
| 仓位大小 | `position_size_pct` | 每笔交易占账户余额的比例 |

---

## 3. 回测系统详解

### 3.1 热力图扫描 (Heatmap Scan) — 参数发现

这是**最重要的第一步**，用于在参数空间中发现盈利高原。

```bash
uv run python strategy/live/hurst_kalman/backtest.py --heatmap
```

#### 扫描范围

| 参数 | 范围 | 说明 |
|------|------|------|
| **X轴: zscore_entry** | 1.5 ~ 5.0 (15个点) | 入场Z-Score阈值。越大 = 越保守 = 交易越少 |
| **Y轴: hurst_window** | 20 ~ 200 (15个点) | Hurst计算窗口。越大 = 越平滑 = 信号越少 |

**固定参数** (扫描时不变):
```
kalman_R = 0.2          # 测量噪声
kalman_Q = 5e-05        # 过程噪声
zscore_window = 60      # Z-Score滚动窗口
mean_reversion_threshold = 0.40
trend_threshold = 0.60
stop_loss_pct = 0.03    # 3%止损
position_size_pct = 0.10 # 10%仓位
only_mean_reversion = True
```

**可选第三维面板** (在多个值间生成独立热力图):
```bash
# 扫描 mean_reversion_threshold = [0.35, 0.40, 0.45]
--heatmap-third-param mean_reversion_threshold

# 扫描 kalman_R = [0.1, 0.2, 0.3]
--heatmap-third-param kalman_R
```

#### 每个网格点的计算

对于每个 `(zscore_entry, hurst_window)` 组合:
1. 构造 `HurstKalmanConfig` + `TradeFilterConfig`
2. `HurstKalmanSignalGenerator.generate()` → 信号数组
3. `VectorizedBacktest.run()` → 权益曲线 + 交易列表
4. `PerformanceAnalyzer.calculate_metrics()` → Sharpe / 年化收益 / 最大回撤 / 胜率 / 交易频率 / Profit Factor

#### Mesa 检测 (盈利高原)

扫描完成后，`MesaDetector` 在 Sharpe 网格上检测稳定盈利区域:

1. **阈值化**: Sharpe > 0.5 的格子标记为盈利
2. **BFS 洪水填充**: 找所有连通的盈利区域
3. **过滤**: 面积 < 3 个格子的碎片丢弃
4. **排序**: 按平均 Sharpe 降序排列
5. **中心点**: 每个 Mesa 取 Sharpe 最高的格子作为中心

每个 Mesa 的元数据:
```
Mesa #0:
  中心参数:    zscore_entry=2.75, hurst_window=122
  Z-Score范围: [1.50, 5.00]
  Hurst范围:   [85, 137]
  面积:        15个格子
  平均Sharpe:  1.71
  稳定性:      0.74  (= 1/(1+std(Sharpe)))
  年化收益:    +11.4%
  最大回撤:    1.6%
  年交易次数:  5.6
  频率分类:    Quarterly (4-12/yr)
```

#### 频率分桶

每个格子根据年化交易次数自动分类:

| 频率带 | 年交易次数 | 典型特征 |
|--------|-----------|---------|
| Daily | > 250 | 高频，需要低延迟和低手续费 |
| Weekly | 50 - 250 | 周频，需持续监控 |
| Bi-Weekly | 25 - 50 | 双周频 |
| Monthly | 12 - 25 | 月频，适合兼职交易者 |
| **Quarterly** | **4 - 12** | **季频，最常见的稳健频率** |
| Yearly | < 4 | 年频，可能不适合活跃管理 |

#### 产出文件

| 文件 | 说明 |
|------|------|
| `heatmap_results.json` | 完整扫描数据 + Mesa列表 + 频率统计。**这是配置系统的核心数据源** |
| `heatmap_report.html` | 6面板交互式Plotly热力图 (Sharpe / 年化收益 / 最大回撤 / 胜率 / 交易频率 / Profit Factor)，叠加Mesa边框和频率等高线 |
| `optimized_config.py` | 从Mesa自动生成的Python配置 (可直接import) |

---

### 3.2 单次回测 (Single Backtest)

用特定的 Mesa 配置跑完整回测:

```bash
# 默认: Mesa #0 (最优Sharpe), 1年
uv run python strategy/live/hurst_kalman/backtest.py

# 指定 Mesa #1, 6个月
uv run python strategy/live/hurst_kalman/backtest.py --mesa 1 --period 6m
```

**输出**: 完整绩效指标 (总收益 / Sharpe / Sortino / Calmar / 最大回撤 / 胜率 / Profit Factor / Funding支出)

---

### 3.3 三阶段完整验证 (Three-Stage Test)

```bash
uv run python strategy/live/hurst_kalman/backtest.py --full --period 1y
```

这是**最严格的验证流程**，测试参数是否真正有效:

#### Stage 1: 样本内优化 (In-Sample Optimization)

- 使用 **80%** 的数据
- Grid Search 搜索最优参数:
  - `hurst_window`: [80, 100, 120]
  - `zscore_entry`: [2.0, 2.5, 3.0, 3.5]
  - `mean_reversion_threshold`: [0.35, 0.40, 0.45]
  - `kalman_R`: [0.1, 0.2, 0.3]
- 以 Sharpe Ratio 为优化目标
- **通过标准**: Sharpe >= 1.0

#### Stage 2: 滚动验证 (Walk-Forward Validation)

- 在同一 80% 数据上滚动测试
- 窗口: 30天训练 + 7天测试，滚动前进
- 每个窗口使用 Stage 1 的最优参数
- **通过标准**:
  - 鲁棒性比率 >= 0.5 (测试收益/训练收益)
  - 正收益窗口比例 >= 50%

#### Stage 3: 样本外测试 (Holdout Test)

- 使用**从未见过的 20%** 数据
- 用 Stage 1 的最优参数直接运行
- 加上市场状态分析 (牛市/熊市/震荡 各阶段的表现)
- **通过标准**:
  - 性能衰减 <= 50% (相对样本内)
  - 样本外 Sharpe >= 0.5

#### 为什么需要三阶段?

```
Stage 1 通过 → 参数在历史上能盈利 (但可能过拟合)
Stage 2 通过 → 参数在不同时间窗口都稳定 (减少过拟合风险)
Stage 3 通过 → 参数在未见数据上仍然有效 (真正的泛化能力)

三阶段全通过 → 策略可用于模拟交易
```

---

### 3.4 单独模式

```bash
# 只跑 Grid Search
uv run python strategy/live/hurst_kalman/backtest.py --optimize

# 只跑 Walk-Forward
uv run python strategy/live/hurst_kalman/backtest.py --walk-forward

# 只跑市场状态分析
uv run python strategy/live/hurst_kalman/backtest.py --regime

# 生成HTML报告
uv run python strategy/live/hurst_kalman/backtest.py --report

# 查看已保存的回测结果
uv run python strategy/live/hurst_kalman/backtest.py --show-results
```

---

## 4. 如何选择策略参数

### 4.1 推荐流程

```
步骤1: 热力图扫描 → 发现参数空间中的盈利区域
步骤2: 阅读热力图 → 理解参数与收益/风险的关系
步骤3: 选择Mesa → 根据你的风险偏好和交易频率
步骤4: 三阶段验证 → 确认参数稳健性
步骤5: 模拟交易 → 真实环境验证
```

### 4.2 热力图怎么看

打开 `heatmap_report.html`，你会看到 6 个热力图面板:

| 面板 | 看什么 |
|------|--------|
| **Sharpe Ratio** | 绿色区域 = 风险调整收益好。找大片连续的绿色区域 (= Mesa) |
| **年化收益率** | 绿色 = 高收益。注意是否与Sharpe图的绿色重合 |
| **最大回撤** | 深色 = 大回撤。优先选择回撤小的区域 |
| **胜率** | 蓝色深 = 胜率高。50%以上是基本要求 |
| **年化交易次数** | 暖色 = 交易频繁。根据你的偏好选择 |
| **Profit Factor** | 绿色 = 盈利因子好。>1.5 为佳 |

**Mesa 边框** (虚线矩形): 系统自动检测的稳定盈利高原，编号从 #0 (最优) 开始。

### 4.3 Mesa 选择决策树

```
你的风险偏好是什么?

├─ 低风险 (保守)
│   → 选择 avg_max_dd_pct 最低的 Mesa
│   → 通常是 hurst_window 较大、zscore_entry 较高的区域
│   → 交易频率低 (Quarterly/Yearly)
│
├─ 中等风险 (平衡)
│   → 选择 avg_sharpe 最高的 Mesa (即 Mesa #0)
│   → Sharpe 综合了收益和风险
│   → 这是系统默认推荐
│
└─ 高风险 (激进)
    → 选择 avg_return_pct 最高的 Mesa
    → 通常 zscore_entry 较低，交易更频繁
    → 注意: 高收益通常伴随高回撤
```

### 4.4 关键指标阈值

| 指标 | 可接受 | 良好 | 优秀 |
|------|--------|------|------|
| Sharpe Ratio | > 0.5 | > 1.0 | > 2.0 |
| 年化收益 | > 5% | > 15% | > 30% |
| 最大回撤 | < 15% | < 10% | < 5% |
| 胜率 | > 40% | > 55% | > 70% |
| Profit Factor | > 1.2 | > 1.5 | > 2.0 |
| 稳定性 (Stability) | > 0.5 | > 0.7 | > 0.9 |

### 4.5 参数含义速查

| 参数 | 增大的效果 | 减小的效果 |
|------|-----------|-----------|
| `zscore_entry` | 更保守，交易更少，每笔利润更高 | 更激进，交易更多，更多假信号 |
| `hurst_window` | 更平滑的状态判断，反应更慢 | 更灵敏的状态判断，更多噪声 |
| `mean_reversion_threshold` | 更多数据被归为均值回复 | 更少数据被归为均值回复(更严格) |
| `kalman_R` | 更信任模型，滤波更平滑 | 更信任观测，跟踪更紧 |
| `stop_loss_pct` | 更宽的止损，更少被止损触发 | 更紧的止损，限制单笔亏损 |
| `min_holding_bars` | 减少过度交易，可能错过退出时机 | 更灵活的退出，可能频繁交易 |

### 4.6 典型参数组合解读

**当前 Mesa #0 配置 (最新扫描结果):**
```
zscore_entry = 2.75     → 中等保守的入场阈值
hurst_window = 122      → 较大的Hurst窗口 (约30小时的15分钟K线)
mean_reversion_threshold = 0.4 → 标准阈值
kalman_R = 0.2          → 中等平滑度
min_holding_bars = 10   → 至少持仓2.5小时
cooldown_bars = 5       → 平仓后冷却1.25小时

→ 季频交易 (5.6次/年)，Sharpe 1.71，年化收益 11.4%，最大回撤 1.6%
→ 特点: 极低回撤，低频，适合不需要频繁操作的交易者
```

### 4.7 不同周期的扫描建议

```bash
# 短期验证 (快速检查)
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --period 6m

# 标准验证 (推荐)
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --period 1y

# 长期验证 (最可靠，但需更多数据)
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --period 2y

# 高分辨率扫描 (更精细但更慢)
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --heatmap-resolution 20

# 带第三维面板 (探索 mean_reversion_threshold 的影响)
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --heatmap-third-param mean_reversion_threshold
```

---

## 5. 从回测到实盘的完整流程

### Step 1: 参数发现

```bash
uv run python strategy/live/hurst_kalman/backtest.py --heatmap --period 1y
```

打开 `heatmap_report.html`，查看:
- 哪些区域有稳定的正 Sharpe?
- 系统检测到几个 Mesa?
- 各 Mesa 的频率和收益特征是什么?

### Step 2: 选择配置

```bash
# 查看所有可用的 Mesa 配置
python -c "from strategy.live.hurst_kalman.configs import list_all_configs; list_all_configs()"
```

输出类似:
```
#    Name                           Sharpe  Stability  Freq                     Z-Score  Hurst
0    Mesa #0 (Quarterly (4-12/yr))    1.71       0.74  Quarterly (4-12/yr)        2.75    122  [BEST]
1    Mesa #1 (Quarterly (4-12/yr))    1.48       0.59  Quarterly (4-12/yr)        5.00     97
```

### Step 3: 单次回测验证

```bash
# 验证 Mesa #0
uv run python strategy/live/hurst_kalman/backtest.py --mesa 0 --period 1y

# 验证 Mesa #1
uv run python strategy/live/hurst_kalman/backtest.py --mesa 1 --period 1y
```

### Step 4: 三阶段严格验证

```bash
uv run python strategy/live/hurst_kalman/backtest.py --full --period 1y
```

**只有三阶段全部通过，才建议进入模拟交易。**

### Step 5: 启动模拟交易

```bash
# 使用 Mesa #0 (默认最优)
uv run python -m strategy.live.hurst_kalman.strategy

# 使用 Mesa #1
uv run python -m strategy.live.hurst_kalman.strategy --mesa 1
```

当前连接的是 **Bitget UTA_DEMO (测试网)**，不会使用真实资金。

### Step 6: 监控模拟交易

```bash
# 查看验证报告
uv run python strategy/live/hurst_kalman/paper_validate.py

# 查看实时日志
tail -f strategy/live/hurst_kalman/hurst_kalman.log
```

验证报告会给出四个建议:
- **CONTINUE**: 无严重问题，继续积累样本
- **INVESTIGATE**: 有警告，需要调查
- **STOP**: 发现严重问题，立即停止
- **READY**: 所有指标达标，可以考虑实盘

### Step 7: 切换实盘 (当 READY 时)

修改 `strategy.py` 中:
- `testnet=True` → `testnet=False`
- `BitgetAccountType.UTA_DEMO` → 生产账户类型
- 确保 `.env` 中配置了正确的实盘 API 密钥

---

## 6. 命令参考

### backtest.py 完整参数

| 参数 | 短写 | 说明 |
|------|------|------|
| `--heatmap` | | 热力图参数扫描 (发现Mesa盈利高原) |
| `--heatmap-resolution N` | | 网格分辨率 (默认15，即15×15=225个点) |
| `--heatmap-third-param X` | | 第三维参数面板: `mean_reversion_threshold` 或 `kalman_R` |
| `--mesa N` | `-m N` | Mesa配置索引 (默认0=最优Sharpe) |
| `--period P` | `-p P` | 时间周期: `3m`/`6m`/`1y`(默认)/`2y` |
| `--full` | `-f` | 三阶段完整验证 (优化+滚动+样本外) |
| `--optimize` | `-o` | 仅Grid Search优化 |
| `--walk-forward` | `-w` | 仅Walk-Forward验证 |
| `--regime` | `-r` | 仅市场状态分析 |
| `--report` | | 生成HTML报告 |
| `--show-results` | `-s` | 显示已保存的回测结果 |
| `--export-config` | `-e` | 导出最优配置 |

### strategy.py 参数

| 参数 | 说明 |
|------|------|
| `--mesa N` | Mesa配置索引 (默认0=最优Sharpe) |

### paper_validate.py 参数

| 参数 | 说明 |
|------|------|
| `--mesa N` | Mesa配置索引 (默认从live_performance.json自动检测) |

---

## 7. 文件说明

### 代码文件

| 文件 | 说明 |
|------|------|
| `core.py` | 核心算法: HurstKalmanConfig, KalmanFilter1D, calculate_hurst |
| `indicator.py` | 实时指标: HurstKalmanIndicator (用于live/paper交易) |
| `configs.py` | 配置加载: get_config(), load_mesa_configs(), StrategyConfig |
| `backtest.py` | 回测主程序: 信号生成、回测引擎、Grid Search、Walk-Forward、三阶段验证 |
| `heatmap.py` | 热力图系统: 参数扫描、Mesa检测、频率分析、HTML报告生成 |
| `strategy.py` | 实时策略: HurstKalmanStrategy (连接Bitget交易所) |
| `performance.py` | 绩效跟踪: PerformanceTracker (live/paper交易) |
| `results.py` | 结果存储: 回测结果持久化 |
| `paper_validate.py` | 模拟验证: 多阶段Paper Trading验证报告 |

### 数据文件

| 文件 | 说明 | 生成方式 |
|------|------|---------|
| `heatmap_results.json` | Mesa配置 + 完整扫描数据 | `--heatmap` |
| `heatmap_report.html` | 交互式热力图报告 | `--heatmap` |
| `optimized_config.py` | Python格式的Mesa配置 | `--heatmap` |
| `backtest_results.json` | 历史回测结果 | 默认模式 / `--full` |
| `backtest_report.html` | 回测HTML报告 | `--report` |
| `live_performance.json` | 实时交易绩效 | strategy.py 运行时 |
| `hurst_kalman.log` | 策略运行日志 | strategy.py 运行时 |
| `strategy_output.log` | 策略标准输出日志 | strategy.py 运行时 |
| `paper_validation_result.json` | 模拟验证结果 | paper_validate.py |

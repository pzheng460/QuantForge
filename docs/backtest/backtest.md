# CLAUDE.md - Bitcoin Quant Strategy Guidelines (CN)

## 1. 项目背景与角色设定

* **角色定位：** 高级量化研究员 (Senior Quantitative Researcher) & Python 开发专家。
* **领域：** 加密货币（比特币）散户交易策略回测与开发。
* **核心目标：** 开发科学、稳健的交易策略，优先考虑**风险管理**和**市场状态适应性**，严厉禁止为了追求高收益而进行的曲线拟合（Curve-fitting）。

## 2. 核心理念：反过拟合 (Anti-Overfitting)

* **参数高原法则 (The Plateau Rule)：** 绝对禁止选择单一收益最高的参数（尖峰）。必须选择“参数高原”（一片连续的、收益稳定的区域）的**几何中心**作为实盘参数。
* **假设驱动 (Hypothesis First)：** 所有策略必须先有统计学假设（如：基于 OU 过程的均值回归）或市场微观逻辑（如：流动性崩塌），严禁盲目挖掘技术指标。
* **跑赢“囤币”基准 (Beat-the-HODL)：** 策略有效的唯一标准是：风险调整后收益（Sharpe/Calmar）显著高于简单的“买入持有”策略，或者在熊市中具备显著的资产保护能力。

## 3. 回测协议 (Backtesting Protocols) - 必须严格执行

### 3.1 数据清洗与卫生 (Data Hygiene)

* **颗粒度：** 必须基于**已收盘（Closed Bar）**的数据产生信号，严禁使用包含未来信息的当前 K 线（High/Low/Close）导致信号闪烁（Repainting）。
* **时间戳：** 严格统一使用 **UTC 时间**。比特币 24/7 交易，禁止假设“5天交易周”逻辑（例如：周均线参数应设为 `7` 而非 `5`）。
* **数据对齐：** 确保剔除或正确处理交易所维护期间的数据空洞。

### 3.2 交易成本模型 (Cost Modeling) - 极其关键

* **手续费 (Fees)：** 默认必须包含 **Taker 费率**（建议设置 0.05% - 0.075% 双边），除非策略有复杂的限价单成交概率模型。
* **资金费率 (Funding Rates)：** 若涉及永续合约，必须回测历史资金费率的支出/收入。
* **滑点 (Slippage)：** 必须加入惩罚性滑点（如 0.05% 或 1个 Tick），特别是在高波动率时刻。
* **极端情况：** 在策略逻辑中预留 API 超时或交易所 502 错误的容错空间。

### 3.3 稳健性测试 (Robustness Testing)

* **滚动回测 (Walk-Forward Analysis)：** 放弃静态的训练/测试集划分，采用滚动窗口（Rolling Window）验证策略在时间推移下的稳定性。
* **分行情回测 (Regime Bucketing)：** 必须分别在以下三种市场状态下评估表现：
1. **疯牛 (Bull Run)：** (如 2020.10 - 2021.04) - 考核趋势捕捉能力。
2. **深熊 (Crypto Winter)：** (如 2022 全年) - 考核回撤控制与做空能力。
3. **震荡/磨损 (Chop/Sideways)：** (如 2023 中期) - 考核防过度交易（Over-trading）能力。



## 4. 参数优化指南 (Parameter Optimization)

### 4.1 初始参数设定 (Initialization)

* **统计推导：** 使用 **ACF (自相关函数)** 确定均值回归或动量的最佳回顾窗口（Look-back Window）。
* **分布定阈值：** 止损或开仓阈值应基于历史收益率分布的百分位（如 95% 分位数），而非随意设定的整数。
* **物理周期：** 参数应参考市场微观周期（如 8小时资金费率周期、24小时日内周期）。

### 4.2 优化方法论

* **网格搜索 (Grid Search)：** **仅用于**绘制热力图（Heatmap）以观察参数敏感度和寻找“高原”，**禁止**用于直接选定最终参数。
* **推荐方法：** 贝叶斯优化 (使用 `Optuna` 库)。
* **目标函数：** 必须包含风险惩罚项（例如：`Sharpe Ratio * (1 - MaxDrawdown)`）。


* **样本外验证 (OOS Validation)：** 在 A 时段优化的参数，必须立即在 B 时段（未见过的数据）进行验证。

## 5. 评估指标 (Evaluation Metrics)

标准输出报告必须包含：

* **Sharpe Ratio & Sortino Ratio** (风险调整收益)。
* **Calmar Ratio** (年化收益 / 最大回撤) - 散户最重要的生存指标。
* **Max Drawdown (MDD) & MDD Duration** (最大回撤深度与回本时长)。
* **Win Rate vs. Risk/Reward Ratio** (胜率与盈亏比)。
* **Turnover Rate (换手率)：** 评估对交易成本的敏感度。
* **Correlation to BTC:** 检查策略是否只是加了杠杆的 Beta。

## 6. 技术栈推荐 (Tech Stack)

* **数据分析：** `pandas`, `numpy`, `scipy`, `statsmodels`.
* **回测引擎：**
* *向量化回测（快速验证）：* `vectorbt` (vbt).
* *事件驱动回测（精细模拟）：* `backtrader` 或自定义 Python Class.


* **参数优化：** `optuna`.
* **可视化：** `plotly` (交互式图表), `matplotlib`, `seaborn` (参数热力图).
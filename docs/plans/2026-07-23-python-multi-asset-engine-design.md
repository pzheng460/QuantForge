# QuantForge Python 多资产引擎设计

日期：2026-07-23  
状态：已确认

## 目标

QuantForge 从 Pine-first 迁移为 Python-first 的事件驱动多资产平台。第一阶段统一支持：

- 美股；
- 美股期权；
- 加密现货；
- 加密永续及期货；
- Backtest、Paper 和 Live；
- Schwab 与现有 CCXT 交易所。

网页不再编辑策略源码，只负责策略选择、参数配置、回测、部署、风险控制、持仓与审计。现有 Pine 策略迁移到 Python，完成逐 Bar 正确性验证后删除 Pine Parser、Runtime、策略文件和代码编辑界面。

## 架构

```text
Data Adapter
  -> MarketEvent
  -> Python Strategy
  -> Intent
  -> Schema Validation
  -> Mandatory Risk Engine
  -> Order Planning
  -> Execution Adapter
  -> Order/Fill Event
  -> Portfolio Ledger
```

回测、模拟和实盘共用 Strategy、Risk、Portfolio 与 Execution 语义，仅替换数据和执行 Adapter。

代码边界：

```text
quantforge/
  strategy/       Python API、配置 Schema、Registry、Context
  domain/         Instrument、Event、Intent、Order、Position
  portfolio/      现金、仓位、PnL、Greeks、保证金
  risk/           通用与资产专属硬风控
  execution/      订单规划、生命周期、幂等与对账
  data/           历史与实时数据端口
  options/        期权链、定价、到期、行权、Roll
  adapters/       Schwab、CCXT、Backtest、Paper
  strategies/     经审核、版本化的 Python 策略
```

## 领域模型

资产类型为 `EQUITY`、`EQUITY_OPTION`、`CRYPTO_SPOT`、`CRYPTO_PERPETUAL` 和 `CRYPTO_FUTURE`。统一 `InstrumentId` 包含 symbol、asset class 和 venue。

期权包含 underlying、expiration、strike、right、style、multiplier 和 settlement。加密衍生品包含 base/quote/settlement currency、contract size、linear/inverse、margin mode、funding interval 和 leverage limits。

策略接收只读 `StrategyContext` 和事件，返回 `OrderIntent`、`MultiLegOrderIntent`、`TargetPositionIntent`、`ClosePositionIntent`、`CancelOrderIntent` 或 `RollOptionIntent`。策略不得直接访问 Broker 或修改账本。

事件至少覆盖 Bar、Quote、Trade、OptionChain、FundingRate、Schedule、Order、CorporateAction、Expiration 和 Assignment。

## 自动实盘风控

不逐笔人工审批，但所有订单必须通过不可绕过的 `RiskEngine`。硬风控包括：

- Live 总开关与 Kill Switch；
- 单笔最大名义金额；
- 单策略资金占用；
- 单标的集中度；
- 每日新开仓次数与每日亏损；
- 最大报价年龄与价差；
- 重复订单检测；
- 数据断连、对账失败和连续 Broker 错误自动停机。

股票默认禁止盘前盘后自动开仓，并检查现金、集中度与公司行动。

美股期权禁止裸卖 Call 和未覆盖 Short Put。Covered Call 预留股票；信用组合必须风险有限；多腿仅在 Broker 支持原子提交时自动执行；否则拒绝，禁止拆腿形成裸仓。处理 DTE、流动性、财报、提前行权、到期、Assignment 和 Roll。

加密现货检查余额、精度和最小下单量。永续限制杠杆、保证金、强平距离、方向敞口，校验 Reduce Only、仓位模式和资金费率。

所有意图使用稳定的 strategy_id、run_id、intent_id 和 client_order_id。启动时对账，状态为 STOPPED、STARTING、RECONCILING、RUNNING、DEGRADED 或 HALTED。HALTED 只允许撤单、平仓和降低风险。

## 数据与期权回测

实盘期权链使用 Schwab。第一阶段历史期权回测使用标的历史价格和波动率模型生成近似报价，结果强制标记 `approximate_unvalidated`。数据端口预留 ORATS、ThetaData 和 Cboe Adapter。

第一阶段支持 Long Call/Put、Covered Call、Protective Put、Collar、风险有限垂直价差、Close、Roll、Expiration 和 Assignment。不支持裸卖、复杂四腿以上结构、真实历史 NBBO 和加密期权。

## 网页

网页展示策略、版本、参数、风险配置、数据源、账户、Backtest/Paper/Live、运行状态、订单、持仓、审计和 Kill Switch。参数由 Pydantic Schema 自动生成。网页不接受 Python 源码。

每次运行保存 strategy version、config snapshot、risk snapshot、data source、environment 和创建时间。

## Pine 迁移与删除

所有现有 Pine 策略迁移为 Python。固定数据集上比较逐 Bar 指标、信号、成交和权益：

- 离散信号完全一致；
- 指标在浮点容差内一致；
- 成交时间、价格和数量一致；
- 最终权益误差不超过 0.01%；
- 不使用调参掩盖语义差异。

若 Pine Runtime 行为错误，以书面规则和回归测试确认 Python 正确语义。所有策略通过后删除 Pine 实现和网页代码编辑器。

## 验证

依次执行领域模型、风控边界、股票/期权/加密账本、到期/行权/Roll 状态机、Broker Adapter、Pine/Python 对照、API、前端构建、只读 Live、Paper 和小额实盘烟雾测试。

实盘烟雾测试会产生真实交易，不以任意标的、数量或时点自动执行；功能实现与只读/Paper 验证完成后，按已配置的硬风控运行。

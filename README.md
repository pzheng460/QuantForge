# QuantForge

QuantForge 是一个 Python-first 多资产量化研究与交易平台，统一支持：

- 美股与 Charles Schwab 实盘接入
- 美股期权链、单腿及 2–4 腿组合订单、到期与 Assignment
- 加密货币现货及永续/期货（CCXT）
- Python 策略回测、参数优化、Paper 与 Live 执行
- 不可绕过的全局订单风控

策略源码由代码库审核和版本管理。网页只提供策略选择、参数、风险配置、
回测和运行控制，不提供在线代码编辑。

## 快速开始

```bash
uv sync
uv run pytest -q
uv run quantforge-cli strategies list
uv run quantforge-cli web start
```

前端：

```bash
cd apps/dashboard/frontend
npm install
npm run build
```

## 核心结构

```text
quantforge/
  strategy/       Python 策略 API、注册表、Bar 与指标
  strategies/     内置技术策略和 TSLA/NVDA 期权管理策略
  backtest/       共享回测引擎
  domain/         股票、期权、加密资产及订单意图
  portfolio/      统一账本
  risk/           强制风险边界
  execution/      唯一订单执行服务
  adapters/       Schwab、CCXT 与行情适配器
  options/        定价、生命周期和期权管理规则
  brokers/        Broker 客户端
```

执行路径固定为：

```text
Python Strategy → Canonical Order Intent → RiskEngine
                → ExecutionService → Schwab / CCXT / Paper
```

## 数据与回测标记

股票和加密回测使用对应市场的历史 OHLCV。Schwab 当前期权链用于实时分析；
历史期权研究使用标的价格与波动率模型近似，并明确标记
`approximate_unvalidated`，不能当作历史 NBBO。

## 安全

密钥只存放在 `.keys/.secrets.toml` 或用户目录的受限 token store，不提交到
Git。Live 订单无需逐单人工确认，但必须通过额度、杠杆、价差、报价时效、
裸期权与每日新增仓位等硬性检查。

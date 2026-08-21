# QuantForge 开发指南

## 架构

QuantForge 是 **Python-first** 平台。交易策略是经过评审的 Python 类，通过
`quantforge.strategy` 注册。没有运行时源码编辑器或内嵌脚本引擎。

规范路径：

```text
Strategy → OrderIntent / MultiLegOrderIntent → RiskEngine
         → ExecutionService → Broker Adapter
```

一等资产：美股、美股期权、加密现货、加密永续/期货。加密期权不在第一阶段。

## 主要模块

- `quantforge/strategy/`：策略 API、注册表、Bar 与指标
- `quantforge/strategies/`：内置经过评审的策略
- `quantforge/backtest/`：共享的 next-bar-open 回测器
- `quantforge/domain/`：标的、意图、事件
- `quantforge/portfolio/`：持仓与现金账本
- `quantforge/risk/`：强制执行的全局风控
- `quantforge/execution/`：唯一的订单提交边界
- `quantforge/adapters/`：CCXT、Schwab、Paper 与行情适配器
- `quantforge/options/`：定价、生命周期、备兑管理器
- `apps/dashboard/`：FastAPI 后端与仅参数的 React UI
- `apps/research/`：研究数据层（DuckDB 仓库 + 事件研究 + 多资产研报，见 `.agents/skills/quantforge-research/SKILL.md`）

## 命令

```bash
uv run pytest -q
uv run ruff check quantforge apps/dashboard/backend test
uv run quantforge-cli strategies list

cd apps/dashboard/frontend
npm run build
```

## 策略规则

- 继承 `Strategy` 或 `BarStrategy`。
- 定义严格的 Pydantic `StrategyConfig`。
- 用 `@register_strategy` 注册。
- 通过生成的 schema 发布参数。
- 不接受来自 HTTP 请求的任意策略源码。
- 回测与实盘引擎必须使用同一策略实现。

## 风控与执行规则

- 策略只产出意图，绝不直接调用 broker API。
- 每笔订单都经过 `ExecutionService` 与 `RiskEngine`。
- 实盘 enable/halt、名义金额、杠杆、价差、期权覆盖、每日新增仓位等限制
  对所有路径不可绕过。
- 报价时效是**自主订单（AUTONOMOUS）的硬门槛**；自主引擎绝不允许关闭新鲜度
  （`require_fresh_quote=True`）。人工指定订单可设 `OrderIntent.operator_override=True`
  以最优可用报价提交（例如流动性差、报价刷新慢的 resting GTC 平仓单）；风控引擎
  仍会保留额度/价差/覆盖/暂停检查，并在观察到该标记时记录显著警告。
- 多腿 Schwab 期权策略必须原子提交。
- 不得把期权 Delta 描述为精确的行权概率。
- 历史模型期权报价必须带 `approximate_unvalidated` 标记。

## 密钥

绝不打印或提交凭证。Schwab OAuth token 存放在 `~/.quantforge/schwab/`
（受限权限）。应用凭证从 `.keys/.secrets.toml` 加载。

## 运行环境与时间约定（实盘操作前必读）

- 服务器本地时区为 **CST（UTC+8）**。`date` 显示 CST；`date -u` 显示 UTC。
  两者相差 8 小时——永远说明你指的是哪个时区。
- `journalctl --user -u quantforge-backend` 打印的是**本地 CST** 时间戳。
- **面向用户的时间一律用 CST**（用户在同一时区）。交易所的 Bar 时间戳与
  `/api/live/*` 的 `created_at` 字段是**绝对 UTC 纪元值**；按相关时区渲染。
- 加密时间框架锚定在 **UTC 整点**。"下一根 Bar 决策"在 N:00 UTC =
  (N+8):00 CST。汇报 Bar 决策时间必须带时区。
- 快速看时间：`uv run python scripts/now.py`——打印 CST、UTC、最新收盘 1h
  Bar，以及两种时区下的下一个决策点。
- 实盘引擎在最近一根已收盘 Bar 上决策，并在下一根 Bar 开盘提交；PollingBarFeed
  每 5s 轮询一次。无持仓且无信号的引擎保持静默——看不到 "Fetched 499 warmup bars"
  属正常噪音。
- 操作实盘引擎前后，先确认全局风控未暂停（`GET /api/risk/global`），并检查
  `GET /api/live/engines` 的状态/错误。

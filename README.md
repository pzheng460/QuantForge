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

## 实盘引擎

- Bar 实盘引擎在收盘 Bar 上决策并以 MARKET 单立即提交；优先使用真实行情
  报价（Schwab bid/ask、CCXT ticker）驱动价差与报价时效风控，行情不可得时
  才回退到 Bar 收盘价的近似估值。
- 引擎退出采用"看门狗"策略：循环静默退出（如行情源挂起）会自动按退避
  重启（上限 3 次，健康运行后计数清零），超过预算或构建失败则标记
  `failed` 并等待人工介入；确定性异常（如风控拒绝）不会自动重启。
- 单实例文件锁防止两个 Dashboard 进程同时下单；所有引擎共享每日新增仓位
  计数（`~/.quantforge/risk/daily-entries.json`），重启不丢。

## 任务持久化

回测/优化任务注册表默认落盘 `~/.quantforge/jobs/registry.json`。设置
`QUANTFORGE_REDIS_HOST`（或 `QUANTFORGE_REDIS_URL`）并安装 `redis` 包后，
注册表改存 Redis；Redis 不可用时自动回退文件后端。起一个本地 Redis 有两
种方式：

- 有 Docker：`docker compose up redis`。
- 无 root（本机已装好，直接 `scripts/dev-redis.sh start`）：项目提供了从
  官方源码编译到 `~/.quantforge/redis` 的无 root 安装（编译于本机执行过），
  启停脚本用法：
  ```bash
  scripts/dev-redis.sh start     # 启动 127.0.0.1:6379（daemon，pidfile 管理）
  scripts/dev-redis.sh status
  scripts/dev-redis.sh stop
  ```

`docker-compose.yml` 中的 PostgreSQL 目前没有应用消费者（尚未有代码读取
或写入它）。

## 安全

密钥只存放在 `.keys/.secrets.toml` 或用户目录的受限 token store，不提交到
Git。Live 订单无需逐单人工确认，但必须通过额度、杠杆、价差、报价时效、
裸期权与每日新增仓位等硬性检查。

Dashboard 后端默认只绑定 `127.0.0.1`（见 `apps/dashboard/start.sh`）。
如需对外提供服务，必须显式 `--host 0.0.0.0` 并设置 `QUANTFORGE_API_KEY`
环境变量——此时所有 `/api*` 请求都需要 `X-API-Key` 请求头（WebSocket 用
`?api_key=` 查询参数），未设置 key 时脚本会拒绝以非 loopback 地址启动。

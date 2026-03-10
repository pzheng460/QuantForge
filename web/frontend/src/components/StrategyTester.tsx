import { useState } from 'react'
import clsx from 'clsx'
import type { BacktestResult, TradeRecord } from '../types'

// ─── Metric helpers ──────────────────────────────────────────────────────────

function pct(v: number | undefined | null, digits = 2, showSign = true): string {
  if (v == null) return '—'
  const sign = showSign && v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function usd(v: number | undefined | null, digits = 2): string {
  if (v == null) return '—'
  const sign = v > 0 ? '+' : v < 0 ? '' : ''
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function num(v: number | undefined | null, digits = 2): string {
  if (v == null) return '—'
  return v.toFixed(digits)
}

function colorClass(v: number | undefined | null): string {
  if (v == null) return 'text-tv-text'
  return v > 0 ? 'text-tv-green' : v < 0 ? 'text-tv-red' : 'text-tv-text'
}

// ─── Metric row ──────────────────────────────────────────────────────────────

function MetricRow({
  label,
  value,
  subvalue,
  valueClass,
}: {
  label: string
  value: string
  subvalue?: string
  valueClass?: string
}) {
  return (
    <div className="tv-metric-row last:border-b-0">
      <span className="tv-metric-label">{label}</span>
      <div className="text-right">
        <span className={clsx('tv-metric-value', valueClass)}>{value}</span>
        {subvalue && <div className="text-[10px] text-tv-muted mt-0.5">{subvalue}</div>}
      </div>
    </div>
  )
}

// ─── Tab: Overview (TV Strategy Tester layout) ───────────────────────────────

function OverviewTab({ r }: { r: BacktestResult }) {
  const netProfit = r.net_profit ?? (r.final_equity - 10000)
  const netProfitPct = r.total_return_pct
  const grossProfit = r.gross_profit ?? r.avg_win * Math.round(r.total_trades * r.win_rate_pct / 100)
  const grossLoss = r.gross_loss ?? Math.abs(r.avg_loss) * Math.round(r.total_trades * (1 - r.win_rate_pct / 100))
  const commissionPaid = r.commission_paid ?? 0
  const numWinning = r.num_winning_trades ?? Math.round(r.total_trades * r.win_rate_pct / 100)
  const numLosing = r.num_losing_trades ?? (r.total_trades - numWinning)

  return (
    <div className="grid grid-cols-2 divide-x divide-tv-border">
      {/* Left column */}
      <div className="pr-4 space-y-0">
        <MetricRow
          label="Net Profit"
          value={usd(netProfit)}
          subvalue={pct(netProfitPct)}
          valueClass={colorClass(netProfit)}
        />
        <MetricRow
          label="Gross Profit"
          value={usd(grossProfit)}
          valueClass="text-tv-green"
        />
        <MetricRow
          label="Gross Loss"
          value={`-$${Math.abs(grossLoss).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          valueClass="text-tv-red"
        />
        <MetricRow
          label="Max Drawdown"
          value={`-$${(r.max_drawdown_pct / 100 * r.final_equity / (1 + r.total_return_pct / 100)).toLocaleString(undefined, { maximumFractionDigits: 2 })}`}
          subvalue={pct(-r.max_drawdown_pct, 2, false)}
          valueClass="text-tv-red"
        />
        <MetricRow
          label="Sharpe Ratio"
          value={num(r.sharpe_ratio)}
          subvalue={r.sharpe_ci_lo != null ? `95% CI [${r.sharpe_ci_lo.toFixed(2)}, ${r.sharpe_ci_hi?.toFixed(2)}]` : undefined}
          valueClass={r.sharpe_ratio >= 1 ? 'text-tv-green' : r.sharpe_ratio < 0 ? 'text-tv-red' : 'text-tv-text'}
        />
        <MetricRow
          label="Sortino Ratio"
          value={num(r.sortino_ratio)}
          valueClass={r.sortino_ratio >= 1 ? 'text-tv-green' : r.sortino_ratio < 0 ? 'text-tv-red' : 'text-tv-text'}
        />
        <MetricRow
          label="Profit Factor"
          value={num(r.profit_factor)}
          valueClass={r.profit_factor >= 1.5 ? 'text-tv-green' : r.profit_factor < 1 ? 'text-tv-red' : 'text-tv-text'}
        />
        <MetricRow
          label="Max Contracts Held"
          value="1"
        />
        <MetricRow
          label="Commission Paid"
          value={commissionPaid > 0 ? `-$${commissionPaid.toLocaleString(undefined, { maximumFractionDigits: 2 })}` : '$0.00'}
          valueClass="text-tv-red"
        />
      </div>

      {/* Right column */}
      <div className="pl-4 space-y-0">
        <MetricRow
          label="Total Closed Trades"
          value={String(r.total_trades)}
        />
        <MetricRow
          label="Total Open Trades"
          value={String(r.total_open_trades ?? 0)}
        />
        <MetricRow
          label="Number Winning Trades"
          value={String(numWinning)}
          valueClass="text-tv-green"
        />
        <MetricRow
          label="Number Losing Trades"
          value={String(numLosing)}
          valueClass="text-tv-red"
        />
        <MetricRow
          label="Percent Profitable"
          value={pct(r.win_rate_pct, 1, false)}
          valueClass={r.win_rate_pct >= 50 ? 'text-tv-green' : 'text-tv-red'}
        />
        <MetricRow
          label="Avg Trade"
          value={r.avg_trade_dollar != null ? usd(r.avg_trade_dollar) : usd((netProfit) / r.total_trades)}
          subvalue={r.avg_trade_pct != null ? pct(r.avg_trade_pct) : undefined}
          valueClass={colorClass(netProfit / r.total_trades)}
        />
        <MetricRow
          label="Avg Winning Trade"
          value={usd(r.avg_win)}
          valueClass="text-tv-green"
        />
        <MetricRow
          label="Avg Losing Trade"
          value={`-$${Math.abs(r.avg_loss).toFixed(2)}`}
          valueClass="text-tv-red"
        />
        <MetricRow
          label="Largest Winning Trade"
          value={usd(r.largest_win)}
          valueClass="text-tv-green"
        />
        <MetricRow
          label="Largest Losing Trade"
          value={`-$${Math.abs(r.largest_loss).toFixed(2)}`}
          valueClass="text-tv-red"
        />
        <MetricRow
          label="Avg # Bars in Trades"
          value={r.avg_bars_held != null ? num(r.avg_bars_held, 1) : `${r.avg_trade_duration_hours.toFixed(1)}h`}
        />
      </div>
    </div>
  )
}

// ─── Tab: Performance Summary ────────────────────────────────────────────────

function PerformanceSummaryTab({ r }: { r: BacktestResult }) {
  return (
    <div className="space-y-4">
      {/* Returns */}
      <div>
        <div className="text-[10px] font-semibold text-tv-muted uppercase tracking-wider mb-2">Returns</div>
        <div className="grid grid-cols-2 divide-x divide-tv-border">
          <div className="pr-4">
            <MetricRow label="Total Return" value={pct(r.total_return_pct)} valueClass={colorClass(r.total_return_pct)} />
            <MetricRow label="Buy & Hold Return" value={pct(r.bh_return_pct)} valueClass={colorClass(r.bh_return_pct)} />
            <MetricRow label="Alpha" value={pct(r.total_return_pct - r.bh_return_pct)} valueClass={colorClass(r.total_return_pct - r.bh_return_pct)} />
            <MetricRow label="Annualized Return" value={pct(r.annualized_return_pct)} valueClass={colorClass(r.annualized_return_pct)} />
          </div>
          <div className="pl-4">
            <MetricRow label="Recovery Factor" value={num(r.recovery_factor)} valueClass={r.recovery_factor > 1 ? 'text-tv-green' : 'text-tv-red'} />
            <MetricRow label="Calmar Ratio" value={num(r.calmar_ratio)} valueClass={r.calmar_ratio >= 1 ? 'text-tv-green' : 'text-tv-red'} />
            <MetricRow label="Annualized Volatility" value={pct(r.annualized_volatility_pct, 2, false)} />
            <MetricRow label="Final Equity" value={`$${r.final_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
          </div>
        </div>
      </div>

      {/* Risk */}
      <div>
        <div className="text-[10px] font-semibold text-tv-muted uppercase tracking-wider mb-2">Risk</div>
        <div className="grid grid-cols-2 divide-x divide-tv-border">
          <div className="pr-4">
            <MetricRow label="Max Drawdown" value={pct(-r.max_drawdown_pct, 2, false)} valueClass="text-tv-red" />
            <MetricRow label="Max DD Duration" value={`${r.max_dd_duration_days.toFixed(1)} days`} />
            <MetricRow label="Sharpe Ratio" value={num(r.sharpe_ratio)} subvalue={r.sharpe_ci_lo != null ? `[${r.sharpe_ci_lo.toFixed(2)}, ${r.sharpe_ci_hi?.toFixed(2)}]` : undefined} valueClass={r.sharpe_ratio >= 1 ? 'text-tv-green' : r.sharpe_ratio < 0 ? 'text-tv-red' : 'text-tv-text'} />
          </div>
          <div className="pl-4">
            <MetricRow label="Sortino Ratio" value={num(r.sortino_ratio)} valueClass={r.sortino_ratio >= 1 ? 'text-tv-green' : 'text-tv-red'} />
            <MetricRow label="Profit Factor" value={num(r.profit_factor)} valueClass={r.profit_factor >= 1.5 ? 'text-tv-green' : r.profit_factor < 1 ? 'text-tv-red' : 'text-tv-text'} />
            <MetricRow label="Payoff Ratio" value={num(r.payoff_ratio)} valueClass={r.payoff_ratio > 1 ? 'text-tv-green' : 'text-tv-red'} />
          </div>
        </div>
      </div>

      {/* Trade Stats */}
      <div>
        <div className="text-[10px] font-semibold text-tv-muted uppercase tracking-wider mb-2">Trade Statistics</div>
        <div className="grid grid-cols-2 divide-x divide-tv-border">
          <div className="pr-4">
            <MetricRow label="Expectancy" value={usd(r.expectancy)} valueClass={colorClass(r.expectancy)} />
            <MetricRow label="Max Consec. Wins" value={String(r.max_consecutive_wins)} valueClass="text-tv-green" />
            <MetricRow label="Max Consec. Losses" value={String(r.max_consecutive_losses)} valueClass="text-tv-red" />
          </div>
          <div className="pl-4">
            <MetricRow label="Avg Trade Duration" value={`${r.avg_trade_duration_hours.toFixed(1)}h`} />
            <MetricRow label="Config" value={r.config_name} />
            <MetricRow label="Period" value={`${r.period_start.slice(0, 10)} → ${r.period_end.slice(0, 10)}`} />
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Tab: Trades List ─────────────────────────────────────────────────────────

function TradesListTab({ trades }: { trades: TradeRecord[] }) {
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 20
  const pages = Math.ceil(trades.length / PAGE_SIZE)
  const paged = trades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  if (trades.length === 0) {
    return <div className="text-center py-8 text-tv-muted text-sm">No trades recorded</div>
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-tv-border">
              {['#', 'Date/Time', 'Type', 'Price', 'Amount', 'Fee', 'Profit', 'P&L %'].map((h) => (
                <th key={h} className="py-1.5 px-2 text-left text-tv-muted font-medium first:pl-0">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((t, i) => {
              const idx = page * PAGE_SIZE + i + 1
              const isBuy = t.side === 'buy'
              return (
                <tr key={i} className="border-b border-tv-border/50 hover:bg-tv-border/30">
                  <td className="py-1.5 px-2 first:pl-0 text-tv-muted">{idx}</td>
                  <td className="py-1.5 px-2 text-tv-muted whitespace-nowrap">
                    {t.timestamp.slice(0, 16).replace('T', ' ')}
                  </td>
                  <td className={clsx('py-1.5 px-2 font-medium', isBuy ? 'text-tv-green' : 'text-tv-red')}>
                    {isBuy ? 'Buy' : 'Sell'}
                  </td>
                  <td className="py-1.5 px-2 tabular-nums text-tv-text">
                    ${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                  </td>
                  <td className="py-1.5 px-2 tabular-nums text-tv-text">{t.amount.toFixed(4)}</td>
                  <td className="py-1.5 px-2 tabular-nums text-tv-muted">${t.fee.toFixed(2)}</td>
                  <td className={clsx('py-1.5 px-2 tabular-nums font-medium', t.pnl >= 0 ? 'text-tv-green' : 'text-tv-red')}>
                    {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                  </td>
                  <td className={clsx('py-1.5 px-2 tabular-nums', t.pnl_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
                    {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between text-xs text-tv-muted pt-1">
          <span>{trades.length} trades total</span>
          <div className="flex items-center gap-1">
            <button
              className="px-2 py-0.5 rounded-sm border border-tv-border hover:bg-tv-border disabled:opacity-40"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              ‹
            </button>
            <span className="px-2">{page + 1} / {pages}</span>
            <button
              className="px-2 py-0.5 rounded-sm border border-tv-border hover:bg-tv-border disabled:opacity-40"
              disabled={page >= pages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              ›
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Tab: Equity Curve ────────────────────────────────────────────────────────

function EquityCurveTab({ r }: { r: BacktestResult }) {
  if (!r.equity_curve.length) return null

  const equities = r.equity_curve.map((p) => p.strategy)
  const bh = r.equity_curve.map((p) => p.bh)
  const minVal = Math.min(...equities, ...bh)
  const maxVal = Math.max(...equities, ...bh)
  const range = maxVal - minVal || 1

  const W = 600
  const H = 120
  const pad = 4

  function toPoints(values: number[]): string {
    return values
      .map((v, i) => {
        const x = pad + (i / (values.length - 1)) * (W - pad * 2)
        const y = H - pad - ((v - minVal) / range) * (H - pad * 2)
        return `${x},${y}`
      })
      .join(' ')
  }

  return (
    <div className="space-y-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: 120 }}>
        <defs>
          <linearGradient id="stratGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2962ff" stopOpacity="0.3" />
            <stop offset="100%" stopColor="#2962ff" stopOpacity="0.0" />
          </linearGradient>
        </defs>
        {/* Strategy fill */}
        <polygon
          points={`${pad},${H - pad} ${toPoints(equities)} ${W - pad},${H - pad}`}
          fill="url(#stratGrad)"
        />
        {/* B&H line */}
        <polyline points={toPoints(bh)} fill="none" stroke="#f59f00" strokeWidth="1" strokeDasharray="4 3" opacity="0.7" />
        {/* Strategy line */}
        <polyline points={toPoints(equities)} fill="none" stroke="#2962ff" strokeWidth="1.5" />
      </svg>
      <div className="flex gap-6 text-xs text-tv-muted">
        <span>
          Start: <strong className="text-tv-text">${r.equity_curve[0]?.strategy.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
        </span>
        <span>
          End: <strong className="text-tv-text">${r.final_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong>
        </span>
        <span>
          {r.period_start.slice(0, 10)} → {r.period_end.slice(0, 10)}
        </span>
      </div>
    </div>
  )
}

// ─── Main StrategyTester component ───────────────────────────────────────────

type Tab = 'overview' | 'performance' | 'trades' | 'equity'

interface Props {
  result: BacktestResult
}

export default function StrategyTester({ result }: Props) {
  const [tab, setTab] = useState<Tab>('overview')

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'performance', label: 'Performance Summary' },
    { id: 'trades', label: 'List of Trades' },
    { id: 'equity', label: 'Equity Curve' },
  ]

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex items-center border-b border-tv-border shrink-0">
        <span className="text-[11px] font-semibold text-tv-muted px-3 py-2 border-r border-tv-border mr-1">
          Strategy Tester
        </span>
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx('tv-tab', tab === t.id && 'tv-tab-active')}
          >
            {t.label}
          </button>
        ))}
        {/* Summary info */}
        <div className="ml-auto flex items-center gap-4 pr-3 text-xs text-tv-muted">
          <span>
            {result.strategy} · {result.exchange}
          </span>
          <span className={clsx('font-medium', result.total_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
            {result.total_return_pct >= 0 ? '+' : ''}{result.total_return_pct.toFixed(2)}%
          </span>
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'overview' && <OverviewTab r={result} />}
        {tab === 'performance' && <PerformanceSummaryTab r={result} />}
        {tab === 'trades' && <TradesListTab trades={result.trades} />}
        {tab === 'equity' && <EquityCurveTab r={result} />}
      </div>
    </div>
  )
}

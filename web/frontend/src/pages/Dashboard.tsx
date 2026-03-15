import { useEffect, useState, useRef } from 'react'
import { api, subscribeLivePerformance } from '../api/client'
import type { LivePerformance, LiveTrade } from '../types'
import { useTimezone, fmtTimeTz } from '../hooks/useTimezone'

function fmt(n: number, digits = 2) {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function fmtPct(n: number) {
  return `${n >= 0 ? '+' : ''}${fmt(n)}%`
}

// fmtTime is now timezone-aware via fmtTimeTz from useTimezone

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string
  sub?: string
  color?: string
}) {
  return (
    <div className="card flex flex-col gap-1">
      <span className="text-xs text-gray-500 uppercase tracking-wide">{label}</span>
      <span className={`text-xl font-bold ${color ?? 'text-gray-900'}`}>{value}</span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  )
}

function TradeTable({ trades, timezone }: { trades: LiveTrade[]; timezone: string }) {
  const [page, setPage] = useState(0)
  const perPage = 10
  // Show most recent first
  const sorted = [...trades].reverse()
  const totalPages = Math.max(1, Math.ceil(sorted.length / perPage))
  const slice = sorted.slice(page * perPage, (page + 1) * perPage)

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b text-left text-gray-500 text-xs uppercase">
              <th className="py-2 px-3">Exit Time</th>
              <th className="py-2 px-3">Symbol</th>
              <th className="py-2 px-3">Side</th>
              <th className="py-2 px-3 text-right">Entry</th>
              <th className="py-2 px-3 text-right">Exit</th>
              <th className="py-2 px-3 text-right">Qty</th>
              <th className="py-2 px-3 text-right">PnL</th>
              <th className="py-2 px-3 text-right">PnL%</th>
              <th className="py-2 px-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {slice.map((t, i) => (
              <tr key={i} className="border-b border-gray-100 hover:bg-gray-50">
                <td className="py-2 px-3 text-gray-600 whitespace-nowrap">
                  {fmtTimeTz(t.exit_time, timezone)}
                </td>
                <td className="py-2 px-3 font-mono text-xs">{t.symbol}</td>
                <td className="py-2 px-3">
                  <span
                    className={`px-2 py-0.5 rounded text-xs font-medium ${
                      t.side === 'long'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-red-100 text-red-700'
                    }`}
                  >
                    {t.side.toUpperCase()}
                  </span>
                </td>
                <td className="py-2 px-3 text-right font-mono">{fmt(t.entry_price)}</td>
                <td className="py-2 px-3 text-right font-mono">{fmt(t.exit_price)}</td>
                <td className="py-2 px-3 text-right font-mono">{fmt(t.amount, 4)}</td>
                <td
                  className={`py-2 px-3 text-right font-mono font-medium ${
                    t.pnl >= 0 ? 'text-green-600' : 'text-red-600'
                  }`}
                >
                  {t.pnl >= 0 ? '+' : ''}
                  {fmt(t.pnl)}
                </td>
                <td
                  className={`py-2 px-3 text-right font-mono ${
                    t.pnl_pct >= 0 ? 'text-green-600' : 'text-red-600'
                  }`}
                >
                  {fmtPct(t.pnl_pct)}
                </td>
                <td className="py-2 px-3 text-gray-500 text-xs max-w-[180px] truncate">
                  {t.exit_reason}
                </td>
              </tr>
            ))}
            {slice.length === 0 && (
              <tr>
                <td colSpan={9} className="py-8 text-center text-gray-400">
                  No trades recorded yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-3 text-sm text-gray-500">
          <span>
            {sorted.length} trades total
          </span>
          <div className="flex gap-2">
            <button
              className="px-3 py-1 rounded border disabled:opacity-40"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              Prev
            </button>
            <span className="px-2 py-1">
              {page + 1} / {totalPages}
            </span>
            <button
              className="px-3 py-1 rounded border disabled:opacity-40"
              disabled={page >= totalPages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function BalanceChart({ trades, timezone }: { trades: LiveTrade[]; timezone: string }) {
  if (trades.length === 0) return null

  // Build cumulative PnL series from trades
  let cumPnl = 0
  const points = trades.map((t) => {
    cumPnl += t.pnl
    return { time: fmtTimeTz(t.exit_time, timezone).slice(5), pnl: cumPnl }
  })

  const maxPnl = Math.max(...points.map((p) => p.pnl), 0)
  const minPnl = Math.min(...points.map((p) => p.pnl), 0)
  const range = maxPnl - minPnl || 1
  const h = 160
  const w = 600
  const pad = 40

  const pathPoints = points.map((p, i) => {
    const x = pad + (i / Math.max(points.length - 1, 1)) * (w - pad * 2)
    const y = h - pad - ((p.pnl - minPnl) / range) * (h - pad * 2)
    return `${i === 0 ? 'M' : 'L'}${x},${y}`
  })

  return (
    <div className="card">
      <h3 className="text-sm font-medium text-gray-700 mb-2">Cumulative PnL</h3>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ maxHeight: 180 }}>
        {/* Zero line */}
        <line
          x1={pad}
          x2={w - pad}
          y1={h - pad - ((0 - minPnl) / range) * (h - pad * 2)}
          y2={h - pad - ((0 - minPnl) / range) * (h - pad * 2)}
          stroke="#d1d5db"
          strokeDasharray="4"
        />
        {/* PnL line */}
        <path
          d={pathPoints.join(' ')}
          fill="none"
          stroke={cumPnl >= 0 ? '#16a34a' : '#dc2626'}
          strokeWidth="2"
        />
        {/* Y axis labels */}
        <text x={pad - 4} y={pad} textAnchor="end" className="text-[10px] fill-gray-400">
          {fmt(maxPnl, 0)}
        </text>
        <text x={pad - 4} y={h - pad + 4} textAnchor="end" className="text-[10px] fill-gray-400">
          {fmt(minPnl, 0)}
        </text>
      </svg>
    </div>
  )
}

export default function DashboardPage() {
  const [perf, setPerf] = useState<LivePerformance | null>(null)
  const [loading, setLoading] = useState(true)
  const [wsConnected, setWsConnected] = useState(false)
  const cleanupRef = useRef<(() => void) | null>(null)
  const { timezone } = useTimezone()

  // Initial fetch
  useEffect(() => {
    api.livePerformance().then((data) => {
      setPerf(data)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  // WebSocket subscription for real-time updates
  useEffect(() => {
    const cleanup = subscribeLivePerformance(
      (msg) => {
        setPerf(msg)
        setWsConnected(true)
      },
      () => setWsConnected(false)
    )
    cleanupRef.current = cleanup
    return () => cleanup()
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin h-8 w-8 border-4 border-brand-500 border-t-transparent rounded-full" />
      </div>
    )
  }

  const noData = !perf || (perf.total_trades === 0 && perf.initial_balance === 0)

  if (noData) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-gray-900">Live Monitoring</h1>
          <ConnectionBadge connected={wsConnected} />
        </div>
        <div className="card py-16 text-center">
          <p className="text-gray-400 text-lg">No live performance data available</p>
          <p className="text-gray-400 text-sm mt-2">
            Start a paper trading session to see live metrics here.
          </p>
          <p className="text-gray-400 text-xs mt-1 font-mono">
            uv run python -m strategy.runner -S ema_crossover --mesa 0
          </p>
        </div>
      </div>
    )
  }

  const p = perf!
  const returnColor = p.total_return_pct >= 0 ? 'text-green-600' : 'text-red-600'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Live Monitoring</h1>
          <p className="text-sm text-gray-500 mt-1">
            Mesa #{p.mesa_index} &middot; {p.config_name || 'Default'} &middot; Since{' '}
            {fmtTimeTz(p.start_time, timezone)}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <ConnectionBadge connected={wsConnected} />
          <span className="text-xs text-gray-400">
            Updated: {fmtTimeTz(p.last_update, timezone)}
          </span>
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <StatCard
          label="Current Balance"
          value={`${fmt(p.current_balance)} USDT`}
          sub={`Initial: ${fmt(p.initial_balance)}`}
        />
        <StatCard
          label="Total PnL"
          value={`${p.total_pnl >= 0 ? '+' : ''}${fmt(p.total_pnl)} USDT`}
          color={returnColor}
          sub={fmtPct(p.total_return_pct)}
        />
        <StatCard
          label="Max Drawdown"
          value={`${fmt(p.max_drawdown_pct)}%`}
          color="text-red-600"
          sub={`Current: ${fmt(p.current_drawdown_pct)}%`}
        />
        <StatCard
          label="Total Trades"
          value={`${p.total_trades}`}
          sub={`${p.winning_trades}W / ${p.losing_trades}L`}
        />
        <StatCard
          label="Win Rate"
          value={`${fmt(p.win_rate_pct, 1)}%`}
          color={p.win_rate_pct >= 50 ? 'text-green-600' : 'text-amber-600'}
        />
        <StatCard
          label="Profit Factor"
          value={fmt(p.profit_factor)}
          color={p.profit_factor >= 1 ? 'text-green-600' : 'text-red-600'}
          sub={`Avg W: ${fmtPct(p.avg_win_pct)} / L: ${fmtPct(p.avg_loss_pct)}`}
        />
      </div>

      {/* Cumulative PnL chart */}
      <BalanceChart trades={p.trades} timezone={timezone} />

      {/* Trade History */}
      <div className="card">
        <h3 className="text-sm font-medium text-gray-700 mb-3">Trade History</h3>
        <TradeTable trades={p.trades} timezone={timezone} />
      </div>
    </div>
  )
}

function ConnectionBadge({ connected }: { connected: boolean }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full text-xs font-medium ${
        connected
          ? 'bg-green-100 text-green-700'
          : 'bg-gray-100 text-gray-500'
      }`}
    >
      <span
        className={`w-2 h-2 rounded-full ${
          connected ? 'bg-green-500 animate-pulse' : 'bg-gray-400'
        }`}
      />
      {connected ? 'Live' : 'Offline'}
    </span>
  )
}

import { useEffect, useState, useRef, useCallback } from 'react'
import clsx from 'clsx'
import { api, subscribeBacktest } from '../api/client'
import type { BacktestRequest, BacktestResult, StrategySchema, Exchange, SchemaField, TradeRecord } from '../types'
import EquityChart from '../components/charts/EquityChart'
import DrawdownChart from '../components/charts/DrawdownChart'
import MonthlyReturnsHeatmap from '../components/charts/MonthlyReturnsHeatmap'
import MetricsPanel from '../components/MetricsPanel'

// ─── Form helpers ────────────────────────────────────────────────────────────

const PERIODS = ['1w', '1m', '3m', '6m', '1y', '2y', '3y', '5y']

function FieldInput({ field, value, onChange }: { field: SchemaField; value: unknown; onChange: (v: unknown) => void }) {
  if (field.type === 'bool') {
    return (
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="w-4 h-4 rounded border-gray-300 text-brand-500"
        />
        <span className="text-sm text-gray-700">{field.label}</span>
      </label>
    )
  }
  if (field.type === 'int' || field.type === 'float') {
    return (
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-500">{field.label}</label>
        <input
          type="number"
          className="input text-sm"
          value={value as number}
          step={field.step ?? (field.type === 'int' ? 1 : 0.01)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          onChange={(e) =>
            onChange(field.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value))
          }
        />
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs text-gray-500">{field.label}</label>
      <input
        type="text"
        className="input text-sm"
        value={value as string}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

// ─── Trade table ─────────────────────────────────────────────────────────────

function TradeTable({ trades }: { trades: TradeRecord[] }) {
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 20
  const total = trades.length
  const paged = trades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const pages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="text-xs w-full">
          <thead>
            <tr className="border-b border-gray-100">
              {['Timestamp', 'Side', 'Price', 'Amount', 'Fee', 'PnL', 'PnL %'].map((h) => (
                <th key={h} className="py-2 px-3 text-left text-gray-400 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {paged.map((t, i) => (
              <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-1.5 px-3 text-gray-500">{t.timestamp.slice(0, 16).replace('T', ' ')}</td>
                <td className="py-1.5 px-3">
                  <span className={clsx('font-medium', t.side === 'buy' ? 'text-green-600' : 'text-red-500')}>
                    {t.side.toUpperCase()}
                  </span>
                </td>
                <td className="py-1.5 px-3 tabular-nums">${t.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                <td className="py-1.5 px-3 tabular-nums">{t.amount.toFixed(4)}</td>
                <td className="py-1.5 px-3 tabular-nums text-gray-500">${t.fee.toFixed(2)}</td>
                <td className={clsx('py-1.5 px-3 tabular-nums font-medium', t.pnl >= 0 ? 'text-green-600' : 'text-red-500')}>
                  {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                </td>
                <td className={clsx('py-1.5 px-3 tabular-nums', t.pnl_pct >= 0 ? 'text-green-600' : 'text-red-500')}>
                  {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-between text-xs text-gray-500 pt-1">
          <span>{total} trades total</span>
          <div className="flex gap-1">
            <button
              className="px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40"
              disabled={page === 0}
              onClick={() => setPage((p) => p - 1)}
            >
              ‹ Prev
            </button>
            <span className="px-2 py-1">
              {page + 1} / {pages}
            </span>
            <button
              className="px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-40"
              disabled={page >= pages - 1}
              onClick={() => setPage((p) => p + 1)}
            >
              Next ›
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Status badge ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const cls: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-700',
    running: 'bg-blue-100 text-blue-700 animate-pulse',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
  }
  return (
    <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', cls[status] ?? 'bg-gray-100 text-gray-600')}>
      {status}
    </span>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

type Tab = 'equity' | 'drawdown' | 'monthly' | 'trades'

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])

  // Form state
  const [strategy, setStrategy] = useState('')
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('')
  const [useDateRange, setUseDateRange] = useState(false)
  const [period, setPeriod] = useState('1y')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [leverage, setLeverage] = useState(1)
  const [mesaIndex, setMesaIndex] = useState(0)
  const [configOverride, setConfigOverride] = useState<Record<string, unknown>>({})
  const [filterOverride, setFilterOverride] = useState<Record<string, unknown>>({})

  // Job / result state
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState<Tab>('equity')

  const wsCleanupRef = useRef<(() => void) | null>(null)

  // Load strategies + exchanges on mount
  useEffect(() => {
    api.strategies().then((data) => {
      setStrategies(data)
      if (data.length > 0) setStrategy(data[0].name)
    })
    api.exchanges().then((data) => {
      setExchanges(data)
    })
  }, [])

  // Build default overrides when strategy changes
  const selectedSchema = strategies.find((s) => s.name === strategy)
  useEffect(() => {
    if (!selectedSchema) return
    const cfg: Record<string, unknown> = {}
    for (const f of selectedSchema.config_fields) cfg[f.name] = f.default
    const flt: Record<string, unknown> = {}
    for (const f of selectedSchema.filter_fields) flt[f.name] = f.default
    setConfigOverride(cfg)
    setFilterOverride(flt)
  }, [strategy])

  // Subscribe to WS when jobId changes
  useEffect(() => {
    if (!jobId) return
    wsCleanupRef.current?.()
    wsCleanupRef.current = subscribeBacktest(
      jobId,
      (msg) => {
        setStatus(msg.status)
        if (msg.status === 'completed' && msg.result) {
          setResult(msg.result)
          setLoading(false)
        } else if (msg.status === 'failed') {
          setError(msg.error ?? 'Unknown error')
          setLoading(false)
        }
      },
      (err) => {
        setError(String(err))
        setLoading(false)
      },
    )
    return () => {
      wsCleanupRef.current?.()
    }
  }, [jobId])

  const handleRun = useCallback(async () => {
    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')

    const req: BacktestRequest = {
      strategy,
      exchange,
      symbol: symbol || undefined,
      leverage,
      mesa_index: mesaIndex,
      config_override: Object.keys(configOverride).length ? configOverride : undefined,
      filter_override: Object.keys(filterOverride).length ? filterOverride : undefined,
    }
    if (useDateRange) {
      req.start_date = startDate || undefined
      req.end_date = endDate || undefined
    } else {
      req.period = period
    }

    try {
      const job = await api.runBacktest(req)
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }, [strategy, exchange, symbol, leverage, mesaIndex, configOverride, filterOverride, useDateRange, period, startDate, endDate])

  const selectedExchange = exchanges.find((e) => e.id === exchange)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Backtest</h1>

      {/* ── Config panel ─────────────────────────────────────────── */}
      <div className="card space-y-5">
        <h2 className="text-sm font-semibold text-gray-700">Configuration</h2>

        {/* Row 1: strategy + exchange */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Strategy</label>
            <select className="input text-sm" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {strategies.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.display_name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Exchange</label>
            <select className="input text-sm" value={exchange} onChange={(e) => setExchange(e.target.value)}>
              {exchanges.map((ex) => (
                <option key={ex.id} value={ex.id}>
                  {ex.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">
              Symbol{' '}
              <span className="text-gray-400">
                (default: {selectedExchange?.default_symbol ?? '…'})
              </span>
            </label>
            <input
              type="text"
              className="input text-sm"
              placeholder={selectedExchange?.default_symbol ?? ''}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Leverage</label>
            <input
              type="number"
              className="input text-sm"
              min={1}
              max={20}
              step={1}
              value={leverage}
              onChange={(e) => setLeverage(Number(e.target.value))}
            />
          </div>
        </div>

        {/* Row 2: period / date range */}
        <div className="flex flex-wrap gap-4 items-end">
          <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
            <input
              type="checkbox"
              checked={useDateRange}
              onChange={(e) => setUseDateRange(e.target.checked)}
              className="w-4 h-4 rounded border-gray-300"
            />
            Custom date range
          </label>
          {!useDateRange ? (
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">Period</label>
              <select className="input text-sm" value={period} onChange={(e) => setPeriod(e.target.value)}>
                {PERIODS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-gray-500">Start date</label>
                <input type="date" className="input text-sm" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-xs text-gray-500">End date</label>
                <input type="date" className="input text-sm" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
              </div>
            </>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Mesa index</label>
            <input
              type="number"
              className="input text-sm w-20"
              min={0}
              step={1}
              value={mesaIndex}
              onChange={(e) => setMesaIndex(parseInt(e.target.value))}
            />
          </div>
        </div>

        {/* Strategy params */}
        {selectedSchema && selectedSchema.config_fields.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
              Strategy Parameters
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
              {selectedSchema.config_fields.map((f) => (
                <FieldInput
                  key={f.name}
                  field={f}
                  value={configOverride[f.name] ?? f.default}
                  onChange={(v) => setConfigOverride((prev) => ({ ...prev, [f.name]: v }))}
                />
              ))}
            </div>
          </div>
        )}

        {/* Filter params */}
        {selectedSchema && selectedSchema.filter_fields.length > 0 && (
          <div>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
              Filter Parameters
            </h3>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
              {selectedSchema.filter_fields.map((f) => (
                <FieldInput
                  key={f.name}
                  field={f}
                  value={filterOverride[f.name] ?? f.default}
                  onChange={(v) => setFilterOverride((prev) => ({ ...prev, [f.name]: v }))}
                />
              ))}
            </div>
          </div>
        )}

        {/* Run button */}
        <div className="flex items-center gap-4 pt-1">
          <button
            className="btn-primary"
            onClick={handleRun}
            disabled={loading || !strategy}
          >
            {loading ? 'Running…' : 'Run Backtest'}
          </button>
          {status && <StatusBadge status={status} />}
        </div>
      </div>

      {/* ── Error ──────────────────────────────────────────────────── */}
      {error && (
        <div className="card border border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700 mb-1">Backtest failed</p>
          <pre className="text-xs text-red-600 whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
        </div>
      )}

      {/* ── Results ────────────────────────────────────────────────── */}
      {result && (
        <div className="space-y-4">
          {/* Metrics */}
          <div className="card">
            <h2 className="text-sm font-semibold text-gray-700 mb-4">
              Results —{' '}
              <span className="font-normal text-gray-500">
                {result.strategy} · {result.exchange} · {result.period_start.slice(0, 10)} → {result.period_end.slice(0, 10)}
              </span>
            </h2>
            <MetricsPanel result={result} />
          </div>

          {/* Chart tabs */}
          <div className="card">
            <div className="flex gap-1 border-b border-gray-100 mb-4 -mx-4 px-4">
              {(['equity', 'drawdown', 'monthly', 'trades'] as Tab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={clsx(
                    'px-3 py-2 text-sm font-medium capitalize border-b-2 -mb-px transition-colors',
                    tab === t
                      ? 'border-brand-500 text-brand-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700',
                  )}
                >
                  {t === 'equity' ? 'Equity' : t === 'drawdown' ? 'Drawdown' : t === 'monthly' ? 'Monthly Returns' : 'Trades'}
                </button>
              ))}
            </div>

            {tab === 'equity' && (
              <div>
                <p className="text-xs text-gray-400 mb-3">
                  Strategy (indigo) vs Buy &amp; Hold (amber dashed) — leveraged {result.exchange} position
                </p>
                <EquityChart data={result.equity_curve} />
              </div>
            )}
            {tab === 'drawdown' && (
              <div>
                <p className="text-xs text-gray-400 mb-3">Drawdown from rolling equity peak</p>
                <DrawdownChart data={result.drawdown_curve} />
              </div>
            )}
            {tab === 'monthly' && (
              <div>
                <p className="text-xs text-gray-400 mb-3">Monthly compounded returns (%)</p>
                <MonthlyReturnsHeatmap data={result.monthly_returns} />
              </div>
            )}
            {tab === 'trades' && (
              <div>
                <p className="text-xs text-gray-400 mb-3">
                  {result.trades.length} closing trades
                </p>
                <TradeTable trades={result.trades} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

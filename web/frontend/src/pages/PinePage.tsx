import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api/client'
import type { BacktestResult, JobStatus } from '../types'

const BASE = '/api'

interface ParseResult {
  valid: boolean
  error?: string
  statement_count: number
  has_strategy: boolean
}

interface TranspileResult {
  success: boolean
  python_code: string
  error?: string
}

const DEFAULT_PINE = `//@version=5
strategy("EMA Cross", overlay=true, initial_capital=100000)
fast_len = input.int(9, title="Fast EMA")
slow_len = input.int(21, title="Slow EMA")
fast_ema = ta.ema(close, fast_len)
slow_ema = ta.ema(close, slow_len)
if ta.crossover(fast_ema, slow_ema)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast_ema, slow_ema)
    strategy.close("Long")
`

export default function PinePage() {
  const [source, setSource] = useState(DEFAULT_PINE)
  const [symbol, setSymbol] = useState('BTC/USDT:USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [startDate, setStartDate] = useState('2026-01-01')
  const [endDate, setEndDate] = useState('2026-03-12')
  const [warmupDays, setWarmupDays] = useState(60)
  const [exchange, setExchange] = useState('bitget')

  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [parseResult, setParseResult] = useState<ParseResult | null>(null)
  const [transpileResult, setTranspileResult] = useState<TranspileResult | null>(null)
  const [activeTab, setActiveTab] = useState<'results' | 'trades' | 'transpile'>('results')
  const [error, setError] = useState<string | null>(null)

  // Job polling
  const [jobId, setJobId] = useState<string | null>(null)
  const cancelledRef = useRef(false)

  useEffect(() => {
    if (!jobId) return
    cancelledRef.current = false
    const poll = async () => {
      while (!cancelledRef.current) {
        try {
          const job: JobStatus = await api.getBacktestStatus(jobId)
          if (cancelledRef.current) break
          setStatus(job.status)
          if (job.status === 'completed' && job.result) {
            setResult(job.result)
            setLoading(false)
            setActiveTab('results')
            break
          } else if (job.status === 'failed') {
            setError(job.error ?? 'Unknown error')
            setLoading(false)
            break
          }
        } catch {
          // transient error, keep polling
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    poll()
    return () => { cancelledRef.current = true }
  }, [jobId])

  const runBacktest = useCallback(async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    setParseResult(null)
    setTranspileResult(null)
    setStatus('pending')
    try {
      const job = await api.runBacktest({
        pine_source: source,
        exchange,
        symbol,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        warmup_days: warmupDays,
      })
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }, [source, symbol, exchange, timeframe, startDate, endDate, warmupDays])

  const validateSyntax = useCallback(async () => {
    setError(null)
    setParseResult(null)
    try {
      const res = await fetch(`${BASE}/pine/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pine_source: source }),
      })
      const data: ParseResult = await res.json()
      setParseResult(data)
    } catch (e) {
      setError(String(e))
    }
  }, [source])

  const transpile = useCallback(async () => {
    setError(null)
    setTranspileResult(null)
    try {
      const res = await fetch(`${BASE}/pine/transpile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pine_source: source }),
      })
      const data: TranspileResult = await res.json()
      if (!data.success) {
        setError(data.error || 'Transpile failed')
      } else {
        setTranspileResult(data)
        setActiveTab('transpile')
      }
    } catch (e) {
      setError(String(e))
    }
  }, [source])

  return (
    <div className="flex h-full gap-0">
      {/* Left: Editor Panel */}
      <div className="w-[480px] shrink-0 flex flex-col border-r border-tv-border bg-tv-panel">
        <div className="px-3 py-2 border-b border-tv-border flex items-center justify-between">
          <span className="text-xs font-medium text-tv-text">Pine Script Editor</span>
          <div className="flex gap-1">
            <button
              onClick={validateSyntax}
              className="px-2 py-1 text-[10px] font-medium rounded bg-tv-border text-tv-text hover:bg-tv-muted/30"
            >
              Validate
            </button>
            <button
              onClick={transpile}
              className="px-2 py-1 text-[10px] font-medium rounded bg-tv-border text-tv-text hover:bg-tv-muted/30"
            >
              Transpile
            </button>
          </div>
        </div>

        {/* Editor textarea */}
        <textarea
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="flex-1 bg-tv-bg text-tv-text text-xs font-mono p-3 resize-none outline-none border-none"
          spellCheck={false}
          placeholder="Enter Pine Script code..."
        />

        {/* Parse validation feedback */}
        {parseResult && (
          <div className={`px-3 py-1.5 text-[10px] border-t border-tv-border ${parseResult.valid ? 'text-green-400 bg-green-900/20' : 'text-red-400 bg-red-900/20'}`}>
            {parseResult.valid
              ? `Valid — ${parseResult.statement_count} statements${parseResult.has_strategy ? ', strategy detected' : ''}`
              : `Error: ${parseResult.error}`}
          </div>
        )}

        {/* Controls */}
        <div className="p-3 border-t border-tv-border space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">Symbol</label>
              <input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              />
            </div>
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">Exchange</label>
              <select
                value={exchange}
                onChange={(e) => setExchange(e.target.value)}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              >
                <option value="bitget">Bitget</option>
                <option value="binance">Binance</option>
                <option value="okx">OKX</option>
                <option value="bybit">Bybit</option>
              </select>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">Timeframe</label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              >
                <option value="1m">1m</option>
                <option value="5m">5m</option>
                <option value="15m">15m</option>
                <option value="1h">1h</option>
                <option value="4h">4h</option>
                <option value="1d">1d</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">Start</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              />
            </div>
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">End</label>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-[10px] text-tv-muted block mb-0.5">Warmup Days</label>
              <input
                type="number"
                value={warmupDays}
                onChange={(e) => setWarmupDays(Number(e.target.value))}
                className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
                min={0}
                max={365}
              />
            </div>
            <div className="flex items-end">
              <button
                onClick={runBacktest}
                disabled={loading}
                className="w-full py-1.5 text-xs font-medium rounded bg-tv-blue text-white hover:bg-tv-blue/80 disabled:opacity-50"
              >
                {loading ? `Running (${status})...` : 'Run Backtest'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Right: Results Panel */}
      <div className="flex-1 flex flex-col bg-tv-bg overflow-hidden">
        {/* Tab bar */}
        <div className="flex border-b border-tv-border bg-tv-panel">
          {(['results', 'trades', 'transpile'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === tab
                  ? 'border-tv-blue text-tv-text'
                  : 'border-transparent text-tv-muted hover:text-tv-text'
              }`}
            >
              {tab === 'results' ? 'Results' : tab === 'trades' ? 'Trades' : 'Python Code'}
            </button>
          ))}
        </div>

        {/* Error display */}
        {error && (
          <div className="m-3 p-3 bg-red-900/20 border border-red-800 rounded text-red-400 text-xs font-mono whitespace-pre-wrap max-h-48 overflow-auto">
            {error}
          </div>
        )}

        {/* Loading state */}
        {loading && (
          <div className="flex-1 flex items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-tv-muted">
              <div className="w-8 h-8 border-2 border-tv-blue border-t-transparent rounded-full animate-spin" />
              <span className="text-sm capitalize">{status || 'Loading...'}</span>
            </div>
          </div>
        )}

        {/* Results tab */}
        {activeTab === 'results' && result && !loading && (
          <div className="p-4 overflow-auto">
            {/* Metrics cards */}
            <div className="grid grid-cols-5 gap-3 mb-4">
              <MetricCard label="Total Return" value={`${result.total_return_pct.toFixed(2)}%`} color={result.total_return_pct >= 0 ? 'text-green-400' : 'text-red-400'} />
              <MetricCard label="Total Trades" value={String(result.total_trades)} />
              <MetricCard label="Win Rate" value={`${result.win_rate_pct.toFixed(1)}%`} />
              <MetricCard label="Profit Factor" value={result.profit_factor.toFixed(2)} />
              <MetricCard label="Max Drawdown" value={`${result.max_drawdown_pct.toFixed(2)}%`} color="text-red-400" />
            </div>
            <div className="grid grid-cols-5 gap-3 mb-6">
              <MetricCard label="Final Equity" value={`$${result.final_equity.toLocaleString(undefined, { minimumFractionDigits: 2 })}`} />
              <MetricCard label="Avg Win" value={`$${result.avg_win.toFixed(2)}`} color="text-green-400" />
              <MetricCard label="Avg Loss" value={`$${result.avg_loss.toFixed(2)}`} color="text-red-400" />
              <MetricCard label="Largest Win" value={`$${result.largest_win.toFixed(2)}`} color="text-green-400" />
              <MetricCard label="Largest Loss" value={`$${result.largest_loss.toFixed(2)}`} color="text-red-400" />
            </div>

            {/* Simple equity curve using SVG */}
            {result.equity_curve.length > 0 && (
              <div className="bg-tv-panel border border-tv-border rounded p-3">
                <div className="text-xs text-tv-muted mb-2">Equity Curve</div>
                <EquityChart data={result.equity_curve.map(p => p.strategy)} />
              </div>
            )}
          </div>
        )}

        {/* Trades tab */}
        {activeTab === 'trades' && result && !loading && (
          <div className="overflow-auto flex-1 p-4">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-tv-muted border-b border-tv-border">
                  <th className="text-left py-1.5 px-2">#</th>
                  <th className="text-left py-1.5 px-2">Side</th>
                  <th className="text-right py-1.5 px-2">Price</th>
                  <th className="text-right py-1.5 px-2">P&L</th>
                  <th className="text-right py-1.5 px-2">P&L %</th>
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t, i) => (
                  <tr key={i} className="border-b border-tv-border/50 hover:bg-tv-panel/50">
                    <td className="py-1.5 px-2 text-tv-muted">{i + 1}</td>
                    <td className={`py-1.5 px-2 font-medium ${t.side === 'long' ? 'text-green-400' : 'text-red-400'}`}>
                      {t.side.toUpperCase()}
                    </td>
                    <td className="py-1.5 px-2 text-right">{t.price.toFixed(2)}</td>
                    <td className={`py-1.5 px-2 text-right font-medium ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {t.pnl >= 0 ? '+' : ''}{t.pnl.toFixed(2)}
                    </td>
                    <td className={`py-1.5 px-2 text-right ${t.pnl_pct >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {t.pnl_pct >= 0 ? '+' : ''}{t.pnl_pct.toFixed(2)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {result.trades.length === 0 && (
              <div className="text-center text-tv-muted py-8">No trades</div>
            )}
          </div>
        )}

        {/* Transpile tab */}
        {activeTab === 'transpile' && !loading && (
          <div className="flex-1 overflow-auto p-4">
            {transpileResult?.python_code ? (
              <pre className="bg-tv-panel border border-tv-border rounded p-3 text-xs font-mono text-tv-text whitespace-pre-wrap overflow-auto">
                {transpileResult.python_code}
              </pre>
            ) : (
              <div className="text-center text-tv-muted py-8">
                Click "Transpile" to convert Pine Script to Python
              </div>
            )}
          </div>
        )}

        {/* Empty state */}
        {!result && !loading && !error && activeTab === 'results' && (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-tv-muted">
              <div className="text-sm mb-1">No results yet</div>
              <div className="text-[10px]">Write Pine Script and click "Run Backtest"</div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function MetricCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="bg-tv-panel border border-tv-border rounded p-2">
      <div className="text-[10px] text-tv-muted mb-0.5">{label}</div>
      <div className={`text-sm font-medium ${color || 'text-tv-text'}`}>{value}</div>
    </div>
  )
}

function EquityChart({ data }: { data: number[] }) {
  if (data.length < 2) return null
  const w = 800
  const h = 200
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - ((v - min) / range) * (h - 20) - 10
    return `${x},${y}`
  }).join(' ')

  const startVal = data[0]
  const endVal = data[data.length - 1]
  const color = endVal >= startVal ? '#26a69a' : '#ef5350'

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-48">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.5"
      />
      {/* Start / end labels */}
      <text x="5" y="15" fill="#848e9c" fontSize="10">
        ${max.toLocaleString(undefined, { minimumFractionDigits: 0 })}
      </text>
      <text x="5" y={h - 5} fill="#848e9c" fontSize="10">
        ${min.toLocaleString(undefined, { minimumFractionDigits: 0 })}
      </text>
    </svg>
  )
}

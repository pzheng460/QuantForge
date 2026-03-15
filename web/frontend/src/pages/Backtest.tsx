import { useEffect, useState, useRef, useCallback } from 'react'
import { api } from '../api/client'
import type { BacktestRequest, BacktestResult, StrategySchema, Exchange, JobStatus } from '../types'
import TradingChart from '../components/chart/TradingChart'
import StrategyTester from '../components/StrategyTester'
import ParameterPanel from '../components/ParameterPanel'

const BASE = '/api'

// ─── Resizable bottom panel ──────────────────────────────────────────────────

function useResizablePanel(defaultHeight: number) {
  const [height, setHeight] = useState(defaultHeight)
  const isDragging = useRef(false)
  const startY = useRef(0)
  const startH = useRef(defaultHeight)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startY.current = e.clientY
    startH.current = height
    e.preventDefault()
  }, [height])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = startY.current - e.clientY
      setHeight(Math.max(160, Math.min(600, startH.current + delta)))
    }
    const onUp = () => { isDragging.current = false }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [])

  return { height, onMouseDown }
}

// ─── Pine Editor panel ───────────────────────────────────────────────────────

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

function PineEditorPanel({
  loading,
  status,
  onRun,
  transpileResult,
  setTranspileResult,
}: {
  loading: boolean
  status: string
  onRun: (req: BacktestRequest) => void
  transpileResult: TranspileResult | null
  setTranspileResult: (v: TranspileResult | null) => void
}) {
  const [source, setSource] = useState(DEFAULT_PINE)
  const [symbol, setSymbol] = useState('BTC/USDT:USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [startDate, setStartDate] = useState('2026-01-01')
  const [endDate, setEndDate] = useState('2026-03-12')
  const [warmupDays, setWarmupDays] = useState(60)
  const [exchange, setExchange] = useState('bitget')
  const [parseResult, setParseResult] = useState<ParseResult | null>(null)

  const handleRun = useCallback(() => {
    onRun({
      pine_source: source,
      exchange,
      symbol,
      timeframe,
      start_date: startDate,
      end_date: endDate,
      warmup_days: warmupDays,
    })
  }, [source, symbol, exchange, timeframe, startDate, endDate, warmupDays, onRun])

  const validateSyntax = useCallback(async () => {
    setParseResult(null)
    try {
      const res = await fetch(`${BASE}/pine/parse`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pine_source: source }),
      })
      setParseResult(await res.json())
    } catch { /* ignore */ }
  }, [source])

  const transpile = useCallback(async () => {
    setTranspileResult(null)
    try {
      const res = await fetch(`${BASE}/pine/transpile`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pine_source: source }),
      })
      const data: TranspileResult = await res.json()
      setTranspileResult(data.success ? data : null)
    } catch { /* ignore */ }
  }, [source, setTranspileResult])

  return (
    <div className="flex flex-col h-full bg-tv-panel border-r border-tv-border overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-tv-border flex items-center justify-between shrink-0">
        <span className="text-[11px] font-semibold text-tv-muted uppercase tracking-wider">Pine Editor</span>
        <div className="flex gap-1">
          <button
            onClick={validateSyntax}
            className="px-2 py-0.5 text-[10px] font-medium rounded bg-tv-border text-tv-text hover:bg-tv-muted/30"
          >
            Validate
          </button>
          <button
            onClick={transpile}
            className="px-2 py-0.5 text-[10px] font-medium rounded bg-tv-border text-tv-text hover:bg-tv-muted/30"
          >
            Transpile
          </button>
        </div>
      </div>

      {/* Editor textarea */}
      <textarea
        value={source}
        onChange={(e) => setSource(e.target.value)}
        className="flex-1 bg-tv-bg text-tv-text text-xs font-mono p-3 resize-none outline-none border-none min-h-0"
        spellCheck={false}
        placeholder="Enter Pine Script code..."
      />

      {/* Parse validation feedback */}
      {parseResult && (
        <div className={`px-3 py-1.5 text-[10px] border-t border-tv-border shrink-0 ${parseResult.valid ? 'text-green-400 bg-green-900/20' : 'text-red-400 bg-red-900/20'}`}>
          {parseResult.valid
            ? `Valid — ${parseResult.statement_count} statements${parseResult.has_strategy ? ', strategy detected' : ''}`
            : `Error: ${parseResult.error}`}
        </div>
      )}

      {/* Controls */}
      <div className="p-3 border-t border-tv-border space-y-2 shrink-0">
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-tv-muted block mb-0.5">Symbol</label>
            <input value={symbol} onChange={(e) => setSymbol(e.target.value)}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none" />
          </div>
          <div>
            <label className="text-[10px] text-tv-muted block mb-0.5">Exchange</label>
            <select value={exchange} onChange={(e) => setExchange(e.target.value)}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none">
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
            <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none">
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
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none" />
          </div>
          <div>
            <label className="text-[10px] text-tv-muted block mb-0.5">End</label>
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] text-tv-muted block mb-0.5">Warmup Days</label>
            <input type="number" value={warmupDays} onChange={(e) => setWarmupDays(Number(e.target.value))}
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text outline-none"
              min={0} max={365} />
          </div>
          <div className="flex items-end">
            <button onClick={handleRun} disabled={loading}
              className="w-full py-1.5 text-xs font-medium rounded bg-tv-blue text-white hover:bg-tv-blue/80 disabled:opacity-50">
              {loading ? `Running (${status})…` : '▶ Run Backtest'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main Backtest page ───────────────────────────────────────────────────────

export default function BacktestPage() {
  const [mode, setMode] = useState<'strategy' | 'editor'>('strategy')
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])

  // Form state (strategy mode)
  const [strategy, setStrategy] = useState('')
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('')
  const [useDateRange, setUseDateRange] = useState(false)
  const [period, setPeriod] = useState('1y')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [leverage, setLeverage] = useState(1)
  const [mesaIndex, setMesaIndex] = useState(0)
  const [configOverride, setConfigOverride] = useState<Record<string, string | number | boolean>>({})
  const [filterOverride, setFilterOverride] = useState<Record<string, string | number | boolean>>({})

  // Job / result state (shared)
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [transpileResult, setTranspileResult] = useState<TranspileResult | null>(null)

  // Resizable bottom panel
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

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
    const cfg: Record<string, string | number | boolean> = {}
    for (const f of selectedSchema.config_fields) if (f.default != null) cfg[f.name] = f.default as string | number | boolean
    const flt: Record<string, string | number | boolean> = {}
    for (const f of selectedSchema.filter_fields) if (f.default != null) flt[f.name] = f.default as string | number | boolean
    setConfigOverride(cfg)
    setFilterOverride(flt)
  }, [strategy])

  // Poll job status
  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    const poll = async () => {
      while (!cancelled) {
        try {
          const job: JobStatus = await api.getBacktestStatus(jobId)
          if (cancelled) break
          setStatus(job.status)
          if (job.status === 'completed' && job.result) {
            setResult(job.result)
            setLoading(false)
            break
          } else if (job.status === 'failed') {
            setError(job.error ?? 'Unknown error')
            setLoading(false)
            break
          }
        } catch {
          // Ignore transient network errors, keep polling
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    poll()
    return () => { cancelled = true }
  }, [jobId])

  // Submit backtest (shared by both modes)
  const submitBacktest = useCallback(async (req: BacktestRequest) => {
    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')
    setTranspileResult(null)
    try {
      const job = await api.runBacktest(req)
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }, [])

  // Strategy mode run handler
  const handleStrategyRun = useCallback(() => {
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
    submitBacktest(req)
  }, [strategy, exchange, symbol, leverage, mesaIndex, configOverride, filterOverride, useDateRange, period, startDate, endDate, submitBacktest])

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 37px)' }}>

      {/* ── Main body: left panel + chart ─────────────────────────── */}
      <div className="flex flex-1 min-h-0">

        {/* Left panel */}
        <div className="w-64 shrink-0 flex flex-col" style={{ height: '100%' }}>
          {/* Mode tabs */}
          <div className="flex border-b border-tv-border bg-tv-panel shrink-0">
            <button
              onClick={() => setMode('strategy')}
              className={`flex-1 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider border-b-2 transition-colors ${
                mode === 'strategy' ? 'border-tv-blue text-tv-text' : 'border-transparent text-tv-muted hover:text-tv-text'
              }`}
            >
              Strategy
            </button>
            <button
              onClick={() => setMode('editor')}
              className={`flex-1 px-3 py-2 text-[10px] font-semibold uppercase tracking-wider border-b-2 transition-colors ${
                mode === 'editor' ? 'border-tv-blue text-tv-text' : 'border-transparent text-tv-muted hover:text-tv-text'
              }`}
            >
              Pine Editor
            </button>
          </div>

          {/* Panel body */}
          <div className="flex-1 min-h-0">
            {mode === 'strategy' ? (
              <ParameterPanel
                strategies={strategies}
                exchanges={exchanges}
                strategy={strategy}
                exchange={exchange}
                symbol={symbol}
                period={period}
                leverage={leverage}
                mesaIndex={mesaIndex}
                useDateRange={useDateRange}
                startDate={startDate}
                endDate={endDate}
                configOverride={configOverride}
                filterOverride={filterOverride}
                loading={loading}
                status={status}
                onStrategyChange={setStrategy}
                onExchangeChange={setExchange}
                onSymbolChange={setSymbol}
                onPeriodChange={setPeriod}
                onLeverageChange={setLeverage}
                onMesaIndexChange={setMesaIndex}
                onUseDateRangeChange={setUseDateRange}
                onStartDateChange={setStartDate}
                onEndDateChange={setEndDate}
                onConfigChange={setConfigOverride}
                onFilterChange={setFilterOverride}
                onRun={handleStrategyRun}
              />
            ) : (
              <PineEditorPanel
                loading={loading}
                status={status}
                onRun={submitBacktest}
                transpileResult={transpileResult}
                setTranspileResult={setTranspileResult}
              />
            )}
          </div>
        </div>

        {/* Right: chart area + bottom panel */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* Chart area */}
          <div className="flex-1 min-h-0 bg-tv-bg relative">
            {result ? (
              <TradingChart
                equityCurve={result.equity_curve}
                trades={result.trades}
                height={undefined}
              />
            ) : (
              <div className="flex items-center justify-center h-full">
                {loading ? (
                  <div className="flex flex-col items-center gap-3 text-tv-muted">
                    <div className="w-8 h-8 border-2 border-tv-blue border-t-transparent rounded-full animate-spin" />
                    <span className="text-sm capitalize">{status || 'Loading…'}</span>
                  </div>
                ) : error ? (
                  <div className="max-w-lg px-6">
                    <p className="text-sm font-medium text-tv-red mb-2">Backtest failed</p>
                    <pre className="text-xs text-tv-muted whitespace-pre-wrap overflow-auto max-h-48 bg-tv-panel border border-tv-border rounded-sm p-3">
                      {error}
                    </pre>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-2 text-tv-muted">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1">
                      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                    </svg>
                    <span className="text-sm">
                      {mode === 'strategy'
                        ? 'Configure parameters and click Run Backtest'
                        : 'Write Pine Script and click Run Backtest'}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Resize handle */}
          {result && (
            <div
              className="h-1 bg-tv-border cursor-row-resize hover:bg-tv-blue transition-colors shrink-0"
              onMouseDown={onDragStart}
            />
          )}

          {/* Bottom Strategy Tester panel */}
          {result && (
            <div
              className="shrink-0 bg-tv-panel border-t border-tv-border overflow-hidden"
              style={{ height: bottomHeight }}
            >
              <StrategyTester result={result} />
            </div>
          )}

          {/* Transpile result (floating panel) */}
          {transpileResult?.python_code && (
            <div className="absolute bottom-4 right-4 w-[500px] max-h-[400px] bg-tv-panel border border-tv-border rounded shadow-lg overflow-hidden z-50 flex flex-col">
              <div className="flex items-center justify-between px-3 py-2 border-b border-tv-border">
                <span className="text-xs font-medium text-tv-text">Transpiled Python</span>
                <button onClick={() => setTranspileResult(null)} className="text-tv-muted hover:text-tv-text text-xs">✕</button>
              </div>
              <pre className="flex-1 p-3 text-xs font-mono text-tv-text whitespace-pre-wrap overflow-auto">
                {transpileResult.python_code}
              </pre>
            </div>
          )}

        </div>
      </div>
    </div>
  )
}

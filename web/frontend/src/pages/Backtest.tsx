import { useEffect, useState, useRef, useCallback } from 'react'
import { api } from '../api/client'
import type { BacktestRequest, BacktestResult, StrategySchema, Exchange, JobStatus } from '../types'
import TradingChart from '../components/chart/TradingChart'
import StrategyTester from '../components/StrategyTester'

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

// ─── Pine parameter parsing ─────────────────────────────────────────────────

interface PineParam {
  name: string
  type: 'int' | 'float'
  value: number
  title: string
  min?: number
  max?: number
  step?: number
}

function parsePineParams(source: string): PineParam[] {
  const params: PineParam[] = []
  const re = /^(\w+)\s*=\s*input\.(int|float)\((.+)\)/gm
  let match
  while ((match = re.exec(source))) {
    const [, name, type, argsStr] = match
    const defMatch = argsStr.match(/^(-?\d+(?:\.\d+)?)/)
    if (!defMatch) continue
    const ptype = type as 'int' | 'float'
    const value = ptype === 'int' ? parseInt(defMatch[1]) : parseFloat(defMatch[1])
    const titleMatch = argsStr.match(/title\s*=\s*"([^"]*)"/)
    const title = titleMatch ? titleMatch[1] : name
    const minMatch = argsStr.match(/minval\s*=\s*(-?\d+(?:\.\d+)?)/)
    const min = minMatch ? parseFloat(minMatch[1]) : undefined
    const maxMatch = argsStr.match(/maxval\s*=\s*(-?\d+(?:\.\d+)?)/)
    const max = maxMatch ? parseFloat(maxMatch[1]) : undefined
    const stepMatch = argsStr.match(/step\s*=\s*(\d+(?:\.\d+)?)/)
    const step = stepMatch ? parseFloat(stepMatch[1]) : undefined
    params.push({ name, type: ptype, value, title, min, max, step })
  }
  return params
}

function updatePineParam(source: string, paramName: string, newValue: number): string {
  const re = new RegExp(
    `(${paramName}\\s*=\\s*input\\.(?:int|float)\\()(-?\\d+(?:\\.\\d+)?)`,
  )
  return source.replace(re, `$1${newValue}`)
}

// ─── Collapsible section ─────────────────────────────────────────────────────

function Section({ title, children, defaultOpen = true, action }: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
  action?: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-b border-tv-border">
      <div className="flex items-center">
        <button
          className="flex-1 flex items-center justify-between px-3 py-2 text-[10px] font-semibold text-tv-muted uppercase tracking-wider hover:text-tv-text transition-colors"
          onClick={() => setOpen((o) => !o)}
        >
          {title}
          <svg
            className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`}
            viewBox="0 0 12 12"
            fill="currentColor"
          >
            <path d="M6 8L1 3h10z" />
          </svg>
        </button>
        {action && <div className="pr-3 shrink-0">{action}</div>}
      </div>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  )
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

const CUSTOM_KEY = '__custom__'

// ─── Main Backtest page ──────────────────────────────────────────────────────

export default function BacktestPage() {
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])

  // Unified state
  const [selectedStrategy, setSelectedStrategy] = useState(CUSTOM_KEY)
  const [source, setSource] = useState(DEFAULT_PINE)
  const [pineParams, setPineParams] = useState<PineParam[]>([])
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('BTC/USDT:USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [startDate, setStartDate] = useState('2026-01-01')
  const [endDate, setEndDate] = useState('2026-03-12')
  const [warmupDays, setWarmupDays] = useState(60)
  // Job / result state
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Resizable bottom panel
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

  // Track whether source change is from param update (skip re-parse)
  const paramUpdateRef = useRef(false)

  // Load strategies + exchanges on mount
  useEffect(() => {
    api.strategies().then((data) => {
      setStrategies(data)
      // Default to first strategy if available
      if (data.length > 0) {
        setSelectedStrategy(data[0].name)
        api.strategySource(data[0].name).then(({ source: src }) => {
          setSource(src)
          setPineParams(parsePineParams(src))
        })
      }
    })
    api.exchanges().then(setExchanges)
  }, [])

  // When selected strategy changes, load its source
  const handleStrategyChange = useCallback((name: string) => {
    setSelectedStrategy(name)
    if (name === CUSTOM_KEY) {
      setSource(DEFAULT_PINE)
      setPineParams(parsePineParams(DEFAULT_PINE))
    } else {
      api.strategySource(name).then(({ source: src }) => {
        setSource(src)
        setPineParams(parsePineParams(src))
      })
    }
  }, [])

  // When source is edited directly, re-parse params
  const handleSourceChange = useCallback((newSource: string) => {
    setSource(newSource)
    if (paramUpdateRef.current) {
      paramUpdateRef.current = false
      return
    }
    setPineParams(parsePineParams(newSource))
  }, [])

  // When a parameter value is changed, update the source
  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev) => {
      const updated = updatePineParam(prev, paramName, newValue)
      return updated
    })
    setPineParams((prev) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p))
    )
  }, [])

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

  // Submit backtest — always sends pine_source
  const handleRun = useCallback(async () => {
    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')
    try {
      const req: BacktestRequest = {
        pine_source: source,
        exchange,
        symbol,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        warmup_days: warmupDays,
      }
      const job = await api.runBacktest(req)
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }, [source, exchange, symbol, timeframe, startDate, endDate, warmupDays])

  const selectedExchange = exchanges.find((e) => e.id === exchange)

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 37px)' }}>
      <div className="flex flex-1 min-h-0">

        {/* ── Left unified panel ──────────────────────────────────────── */}
        <div className="w-80 shrink-0 flex flex-col bg-tv-panel border-r border-tv-border" style={{ height: '100%' }}>

          {/* Header */}
          <div className="px-3 py-2 border-b border-tv-border flex items-center justify-between shrink-0">
            <span className="text-[11px] font-semibold text-tv-muted uppercase tracking-wider">Backtest</span>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto min-h-0 flex flex-col">

            {/* Strategy selector */}
            <Section title="Strategy">
              <div className="space-y-1">
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Strategy</label>
                  <select
                    className="tv-select text-xs py-1"
                    value={selectedStrategy}
                    onChange={(e) => handleStrategyChange(e.target.value)}
                  >
                    <option value={CUSTOM_KEY}>Custom Script</option>
                    {strategies.map((s) => (
                      <option key={s.name} value={s.name}>{s.display_name}</option>
                    ))}
                  </select>
                </div>
              </div>
            </Section>

            {/* Pine Script editor */}
            <Section title="Pine Script">
              <div className="flex flex-col gap-1">
                <textarea
                  value={source}
                  onChange={(e) => handleSourceChange(e.target.value)}
                  className="w-full bg-tv-bg text-tv-text text-[11px] font-mono p-2 rounded border border-tv-border resize-y outline-none focus:border-tv-blue/50"
                  spellCheck={false}
                  placeholder="Enter Pine Script code..."
                  rows={12}
                  style={{ minHeight: 120, maxHeight: 400 }}
                />
              </div>
            </Section>

            {/* Parameters (auto-extracted from Pine source) */}
            {pineParams.length > 0 && (
              <Section title={`Parameters (${pineParams.length})`}>
                <div className="space-y-0">
                  {pineParams.map((p) => (
                    <div key={p.name} className="flex flex-col gap-0.5 py-1">
                      <label className="text-[10px] text-tv-muted">{p.title}</label>
                      <input
                        type="number"
                        className="tv-input text-xs py-1"
                        value={p.value}
                        step={p.step ?? (p.type === 'int' ? 1 : 0.01)}
                        min={p.min ?? undefined}
                        max={p.max ?? undefined}
                        onChange={(e) => {
                          const v = p.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value)
                          if (!isNaN(v)) handleParamChange(p.name, v)
                        }}
                      />
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* Backtest settings */}
            <Section title="Settings">
              <div className="space-y-1">
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Exchange</label>
                  <select className="tv-select text-xs py-1" value={exchange} onChange={(e) => setExchange(e.target.value)}>
                    {exchanges.map((ex) => (
                      <option key={ex.id} value={ex.id}>{ex.name}</option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">
                    Symbol <span className="text-tv-border">({selectedExchange?.default_symbol ?? '…'})</span>
                  </label>
                  <input
                    type="text"
                    className="tv-input text-xs py-1"
                    placeholder={selectedExchange?.default_symbol ?? ''}
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Timeframe</label>
                  <select className="tv-select text-xs py-1" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    <option value="1m">1m</option>
                    <option value="5m">5m</option>
                    <option value="15m">15m</option>
                    <option value="1h">1h</option>
                    <option value="4h">4h</option>
                    <option value="1d">1d</option>
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-0.5 py-1">
                    <label className="text-[10px] text-tv-muted">Start Date</label>
                    <input type="date" className="tv-input text-xs py-1" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                  </div>
                  <div className="flex flex-col gap-0.5 py-1">
                    <label className="text-[10px] text-tv-muted">End Date</label>
                    <input type="date" className="tv-input text-xs py-1" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                  </div>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Warmup Days</label>
                  <input
                    type="number"
                    className="tv-input text-xs py-1"
                    value={warmupDays}
                    onChange={(e) => setWarmupDays(Number(e.target.value))}
                    min={0} max={365}
                  />
                </div>
              </div>
            </Section>
          </div>

          {/* Run button at bottom */}
          <div className="shrink-0 border-t border-tv-border p-3 space-y-2">
            {status && loading && (
              <div className={`text-xs text-center capitalize ${status === 'running' ? 'text-tv-blue animate-pulse' : 'text-tv-muted'}`}>
                {status === 'running' ? '● Running…' : status}
              </div>
            )}
            <button
              className="tv-btn-primary w-full"
              onClick={handleRun}
              disabled={loading || !source.trim()}
            >
              {loading ? 'Running…' : '▶ Run Backtest'}
            </button>
          </div>
        </div>

        {/* ── Right: chart area + bottom panel ────────────────────────── */}
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
                    <span className="text-sm">Select a strategy or write Pine Script, then click Run Backtest</span>
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

        </div>
      </div>
    </div>
  )
}

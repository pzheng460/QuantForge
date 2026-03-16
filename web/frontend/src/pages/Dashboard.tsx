import { useEffect, useState, useRef, useCallback } from 'react'
import { api, subscribeLivePerformance } from '../api/client'
import type {
  LivePerformance,
  LiveEngineOut,
  LiveStartRequest,
  StrategySchema,
  Exchange,
  BacktestResult,
} from '../types'
import StrategyTester from '../components/StrategyTester'
import { livePerformanceToBacktestResult } from '../utils/liveAdapter'

// ─── Pine parameter parsing (shared with Backtest) ──────────────────────────

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

// ─── Collapsible section ────────────────────────────────────────────────────

function Section({ title, children, defaultOpen = true }: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-b border-tv-border">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-semibold text-tv-muted uppercase tracking-wider hover:text-tv-text transition-colors"
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

// ─── Status badge ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    warmup: 'bg-yellow-500/20 text-yellow-400',
    running: 'bg-tv-green/20 text-tv-green',
    stopped: 'bg-tv-muted/20 text-tv-muted',
    failed: 'bg-tv-red/20 text-tv-red',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[10px] font-medium ${styles[status] || styles.stopped}`}>
      {status === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-tv-green animate-pulse" />}
      {status === 'warmup' && <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />}
      {status.toUpperCase()}
    </span>
  )
}

// ─── Main Live Trading page ─────────────────────────────────────────────────

export default function DashboardPage() {
  // Strategy setup state
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])
  const [selectedStrategy, setSelectedStrategy] = useState(CUSTOM_KEY)
  const [source, setSource] = useState(DEFAULT_PINE)
  const [pineParams, setPineParams] = useState<PineParam[]>([])
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('BTC/USDT:USDT')
  const [timeframe, setTimeframe] = useState('1h')
  const [positionSize, setPositionSize] = useState(100)
  const [leverage, setLeverage] = useState(1)
  const [warmupBars, setWarmupBars] = useState(500)
  const [demo, setDemo] = useState(true)

  // Engine state
  const [engines, setEngines] = useState<LiveEngineOut[]>([])
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  // Live performance & adapted result
  const [perf, setPerf] = useState<LivePerformance | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const cleanupRef = useRef<(() => void) | null>(null)

  const paramUpdateRef = useRef(false)

  // Active engine (first running/warmup engine)
  const activeEngine = engines.find((e) => e.status === 'running' || e.status === 'warmup')

  // Load strategies + exchanges + engines on mount
  useEffect(() => {
    api.strategies().then((data) => {
      setStrategies(data)
      if (data.length > 0) {
        setSelectedStrategy(data[0].name)
        api.strategySource(data[0].name).then(({ source: src }) => {
          setSource(src)
          setPineParams(parsePineParams(src))
        })
      }
    })
    api.exchanges().then(setExchanges)
    api.liveEngines().then(setEngines).catch(() => {})
  }, [])

  // WebSocket subscription for real-time performance
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

  // Also poll engines list periodically to catch status changes
  useEffect(() => {
    const interval = setInterval(() => {
      api.liveEngines().then(setEngines).catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  // Strategy selector
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

  const handleSourceChange = useCallback((newSource: string) => {
    setSource(newSource)
    if (paramUpdateRef.current) {
      paramUpdateRef.current = false
      return
    }
    setPineParams(parsePineParams(newSource))
  }, [])

  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev) => updatePineParam(prev, paramName, newValue))
    setPineParams((prev) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p))
    )
  }, [])

  // Start live engine
  const handleStart = useCallback(async () => {
    setStarting(true)
    setStartError(null)
    try {
      const req: LiveStartRequest = {
        pine_source: source,
        exchange,
        symbol,
        timeframe,
        demo,
        position_size_usdt: positionSize,
        leverage,
        warmup_bars: warmupBars,
      }
      if (selectedStrategy !== CUSTOM_KEY) {
        req.strategy = selectedStrategy
      }
      await api.startLive(req)
      // Refresh engines list
      const updated = await api.liveEngines()
      setEngines(updated)
    } catch (e) {
      setStartError(String(e))
    } finally {
      setStarting(false)
    }
  }, [source, exchange, symbol, timeframe, demo, positionSize, leverage, warmupBars, selectedStrategy])

  // Stop live engine
  const handleStop = useCallback(async (engineId: string) => {
    try {
      await api.stopLive(engineId)
      const updated = await api.liveEngines()
      setEngines(updated)
    } catch (e) {
      setStartError(String(e))
    }
  }, [])

  // Convert live performance to BacktestResult for StrategyTester
  const adaptedResult: BacktestResult | null = perf
    ? livePerformanceToBacktestResult(perf, {
        exchange: activeEngine?.exchange,
        strategy: activeEngine?.strategy,
      })
    : null

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 37px)' }}>
      <div className="flex flex-1 min-h-0">

        {/* ── Left panel: strategy selector + settings ─────────────── */}
        <div className="w-80 shrink-0 flex flex-col bg-tv-panel border-r border-tv-border" style={{ height: '100%' }}>

          {/* Header */}
          <div className="px-3 py-2 border-b border-tv-border flex items-center justify-between shrink-0">
            <span className="text-[11px] font-semibold text-tv-muted uppercase tracking-wider">Live Trading</span>
            {activeEngine && <StatusBadge status={activeEngine.status} />}
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
                    disabled={!!activeEngine}
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
            <Section title="Pine Script" defaultOpen={!activeEngine}>
              <textarea
                value={source}
                onChange={(e) => handleSourceChange(e.target.value)}
                className="w-full bg-tv-bg text-tv-text text-[11px] font-mono p-2 rounded border border-tv-border resize-y outline-none focus:border-tv-blue/50"
                spellCheck={false}
                rows={10}
                style={{ minHeight: 100, maxHeight: 300 }}
                disabled={!!activeEngine}
              />
            </Section>

            {/* Parameters */}
            {pineParams.length > 0 && (
              <Section title={`Parameters (${pineParams.length})`} defaultOpen={!activeEngine}>
                <div className="space-y-0">
                  {pineParams.map((p) => (
                    <div key={p.name} className="flex flex-col gap-0.5 py-1">
                      <label className="text-[10px] text-tv-muted">{p.title}</label>
                      <input
                        type="number"
                        className="tv-input text-xs py-1"
                        value={p.value}
                        step={p.step ?? (p.type === 'int' ? 1 : 0.01)}
                        min={p.min}
                        max={p.max}
                        disabled={!!activeEngine}
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

            {/* Settings */}
            <Section title="Settings">
              <div className="space-y-1">
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Exchange</label>
                  <select className="tv-select text-xs py-1" value={exchange} onChange={(e) => setExchange(e.target.value)} disabled={!!activeEngine}>
                    {exchanges.map((ex) => (
                      <option key={ex.id} value={ex.id}>{ex.name}</option>
                    ))}
                  </select>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Symbol</label>
                  <input className="tv-input text-xs py-1" value={symbol} onChange={(e) => setSymbol(e.target.value)} disabled={!!activeEngine} />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Timeframe</label>
                  <select className="tv-select text-xs py-1" value={timeframe} onChange={(e) => setTimeframe(e.target.value)} disabled={!!activeEngine}>
                    {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
                      <option key={tf} value={tf}>{tf}</option>
                    ))}
                  </select>
                </div>
                <div className="flex gap-2">
                  <div className="flex-1 flex flex-col gap-0.5 py-1">
                    <label className="text-[10px] text-tv-muted">Position Size (USDT)</label>
                    <input type="number" className="tv-input text-xs py-1" value={positionSize} onChange={(e) => setPositionSize(Number(e.target.value))} disabled={!!activeEngine} />
                  </div>
                  <div className="flex-1 flex flex-col gap-0.5 py-1">
                    <label className="text-[10px] text-tv-muted">Leverage</label>
                    <input type="number" className="tv-input text-xs py-1" value={leverage} min={1} max={100} onChange={(e) => setLeverage(Number(e.target.value))} disabled={!!activeEngine} />
                  </div>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Warmup Bars</label>
                  <input type="number" className="tv-input text-xs py-1" value={warmupBars} onChange={(e) => setWarmupBars(Number(e.target.value))} disabled={!!activeEngine} />
                </div>
                <div className="flex items-center gap-2 py-1">
                  <input type="checkbox" id="demo-toggle" checked={demo} onChange={(e) => setDemo(e.target.checked)} disabled={!!activeEngine} className="rounded" />
                  <label htmlFor="demo-toggle" className="text-[10px] text-tv-muted">Demo Mode (Sandbox)</label>
                </div>
              </div>
            </Section>

            {/* Running engines list */}
            {engines.length > 0 && (
              <Section title={`Engines (${engines.length})`}>
                <div className="space-y-1">
                  {engines.map((eng) => (
                    <div key={eng.engine_id} className="flex items-center justify-between py-1 px-1 rounded hover:bg-tv-bg/50">
                      <div className="flex-1 min-w-0">
                        <div className="text-[11px] text-tv-text truncate">{eng.strategy}</div>
                        <div className="text-[9px] text-tv-muted">{eng.symbol} {eng.timeframe} {eng.demo ? 'DEMO' : 'LIVE'}</div>
                      </div>
                      <StatusBadge status={eng.status} />
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </div>

          {/* Bottom button */}
          <div className="px-3 py-2 border-t border-tv-border shrink-0">
            {startError && (
              <div className="text-[10px] text-tv-red mb-1 truncate" title={startError}>
                {startError}
              </div>
            )}
            {activeEngine ? (
              <button
                className="w-full py-1.5 rounded text-xs font-semibold bg-tv-red/20 text-tv-red hover:bg-tv-red/30 transition-colors"
                onClick={() => handleStop(activeEngine.engine_id)}
              >
                Stop {activeEngine.strategy}
              </button>
            ) : (
              <button
                className="w-full py-1.5 rounded text-xs font-semibold bg-tv-blue text-white hover:bg-tv-blue/80 transition-colors disabled:opacity-40"
                disabled={!source.trim() || starting}
                onClick={handleStart}
              >
                {starting ? 'Starting...' : 'Start Live Trading'}
              </button>
            )}
          </div>
        </div>

        {/* ── Right panel: strategy report ────────────────────────── */}
        <div className="flex-1 flex flex-col min-w-0 bg-tv-bg">
          {activeEngine ? (
            <>
              {/* Engine info bar */}
              <div className="px-4 py-2 border-b border-tv-border flex items-center gap-3 bg-tv-panel shrink-0">
                <StatusBadge status={activeEngine.status} />
                <span className="text-xs text-tv-text font-medium">{activeEngine.strategy}</span>
                <span className="text-[10px] text-tv-muted">{activeEngine.symbol}</span>
                <span className="text-[10px] text-tv-muted">{activeEngine.timeframe}</span>
                <span className="text-[10px] text-tv-muted">{activeEngine.exchange}</span>
                {activeEngine.demo && <span className="text-[9px] px-1.5 py-0.5 rounded bg-yellow-500/20 text-yellow-400">DEMO</span>}
                {activeEngine.leverage > 1 && <span className="text-[9px] px-1.5 py-0.5 rounded bg-tv-blue/20 text-tv-blue">{activeEngine.leverage}x</span>}
                <div className="flex-1" />
                <span className={`w-2 h-2 rounded-full ${wsConnected ? 'bg-tv-green animate-pulse' : 'bg-tv-muted'}`} />
                <span className="text-[9px] text-tv-muted">{wsConnected ? 'Connected' : 'Offline'}</span>
              </div>

              {/* Strategy Tester */}
              <div className="flex-1 overflow-auto">
                {adaptedResult && adaptedResult.total_trades > 0 ? (
                  <StrategyTester result={adaptedResult} />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <div className="text-center">
                      <div className="text-tv-muted text-sm">
                        {activeEngine.status === 'warmup' ? 'Warming up indicators...' : 'Waiting for trades...'}
                      </div>
                      <div className="text-tv-muted/60 text-xs mt-1">
                        The strategy report will appear once the first trade is completed.
                      </div>
                      {perf && (
                        <div className="mt-4 text-[10px] text-tv-muted/80 space-y-0.5">
                          <div>Balance: {perf.current_balance.toLocaleString()} USDT</div>
                          <div>Last update: {perf.last_update || '—'}</div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            /* No active engine — show instructions */
            <div className="flex items-center justify-center h-full">
              <div className="text-center max-w-md">
                <div className="text-tv-muted text-lg mb-2">No Live Engine Running</div>
                <div className="text-tv-muted/60 text-xs leading-relaxed">
                  Select a strategy, configure settings, and click "Start Live Trading"
                  to begin. The strategy report will update in real-time as trades are executed.
                </div>
                {engines.filter((e) => e.status === 'stopped' || e.status === 'failed').length > 0 && (
                  <div className="mt-4 text-[10px] text-tv-muted/50">
                    {engines.filter((e) => e.status === 'stopped').length} stopped engine(s)
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

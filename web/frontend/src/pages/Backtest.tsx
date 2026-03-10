import { useEffect, useState, useRef, useCallback } from 'react'
import { api, subscribeBacktest } from '../api/client'
import type { BacktestRequest, BacktestResult, StrategySchema, Exchange } from '../types'
import TradingChart from '../components/chart/TradingChart'
import StrategyTester from '../components/StrategyTester'
import ParameterPanel from '../components/ParameterPanel'

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

// ─── Main Backtest page ───────────────────────────────────────────────────────

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
  const [configOverride, setConfigOverride] = useState<Record<string, string | number | boolean>>({})
  const [filterOverride, setFilterOverride] = useState<Record<string, string | number | boolean>>({})

  // Job / result state
  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  // Resizable bottom panel
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

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
    const cfg: Record<string, string | number | boolean> = {}
    for (const f of selectedSchema.config_fields) if (f.default != null) cfg[f.name] = f.default as string | number | boolean
    const flt: Record<string, string | number | boolean> = {}
    for (const f of selectedSchema.filter_fields) if (f.default != null) flt[f.name] = f.default as string | number | boolean
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
    return () => { wsCleanupRef.current?.() }
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

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 37px)' }}>

      {/* ── Main body: left panel + chart ─────────────────────────── */}
      <div className="flex flex-1 min-h-0">

        {/* Left parameter panel (fixed width) */}
        <div className="w-56 shrink-0" style={{ height: '100%' }}>
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
            onRun={handleRun}
          />
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
                    <span className="text-sm">Configure parameters and click Run Backtest</span>
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

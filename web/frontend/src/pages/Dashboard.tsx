import { useEffect, useState, useRef, useCallback } from 'react'
import { ChevronDown } from 'lucide-react'
import { api, subscribeLivePerformance } from '../api/client'
import { useDashboardStore, CUSTOM_KEY, DEFAULT_PINE } from '../stores/dashboardStore'
import { useCatalog } from '../hooks/useCatalog'
import type {
  LivePerformance,
  LiveEngineOut,
  LiveStartRequest,
  BacktestResult,
  StrategySchema,
  Exchange,
} from '../types'
import StrategyTester from '../components/StrategyTester'
import { livePerformanceToBacktestResult } from '../utils/liveAdapter'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible'

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

function Section({
  title,
  defaultOpen = true,
  children,
}: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <div className="border-b border-border">
        <CollapsibleTrigger asChild>
          <button className="flex w-full items-center justify-between px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors">
            {title}
            <ChevronDown
              className={cn(
                'h-3 w-3 transition-transform',
                open && 'rotate-180',
              )}
            />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent className="px-3 pb-3">
          {children}
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

// ─── Status badge ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const variantMap: Record<string, 'success' | 'warning' | 'secondary' | 'destructive'> = {
    running: 'success',
    warmup: 'warning',
    stopped: 'secondary',
    failed: 'destructive',
  }
  const variant = variantMap[status] || 'secondary'

  return (
    <Badge variant={variant} className="gap-1.5 text-[10px]">
      {status === 'running' && (
        <span className="w-1.5 h-1.5 rounded-full bg-tv-green animate-pulse" />
      )}
      {status === 'warmup' && (
        <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />
      )}
      {status.toUpperCase()}
    </Badge>
  )
}

// ─── Main Live Trading page ─────────────────────────────────────────────────

export default function DashboardPage() {
  const { strategies, exchanges } = useCatalog()

  // Zustand store (persists across route changes)
  const {
    selectedStrategy, setSelectedStrategy,
    source, setSource,
    pineParams, setPineParams,
    exchange, setExchange,
    symbol, setSymbol,
    timeframe, setTimeframe,
    positionSize, setPositionSize,
    leverage, setLeverage,
    warmupBars, setWarmupBars,
    demo, setDemo,
    engines, setEngines,
    starting, setStarting,
    startError, setStartError,
    perf, setPerf,
    wsConnected, setWsConnected,
    initialized, setInitialized,
  } = useDashboardStore()

  const cleanupRef = useRef<(() => void) | null>(null)
  const paramUpdateRef = useRef(false)

  // Active engine (first running / warmup)
  const activeEngine = engines.find(
    (e) => e.status === 'running' || e.status === 'warmup',
  )

  // Set default strategy + load engines on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      // Don't auto-select — let the user choose
      setInitialized(true)
    }
  }, [strategies, initialized])

  // Load live engines on mount
  useEffect(() => {
    api.liveEngines().then(setEngines).catch(() => {})
  }, [])

  // WebSocket subscription for real-time performance
  useEffect(() => {
    const cleanup = subscribeLivePerformance(
      (msg) => {
        setPerf(msg)
        setWsConnected(true)
      },
      () => setWsConnected(false),
    )
    cleanupRef.current = cleanup
    return () => cleanup()
  }, [])

  // Poll engines list periodically
  useEffect(() => {
    const interval = setInterval(() => {
      api.liveEngines().then(setEngines).catch(() => {})
    }, 5000)
    return () => clearInterval(interval)
  }, [])

  const handleStrategyChange = useCallback(
    (name: string) => {
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
    },
    [],
  )

  const handleSourceChange = useCallback((val: string) => {
    setSource(val)
    if (paramUpdateRef.current) {
      paramUpdateRef.current = false
      return
    }
    setPineParams(parsePineParams(val))
  }, [])

  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev) => updatePineParam(prev, paramName, newValue))
    setPineParams((prev) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p)),
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
        position_size_usdt: positionSize,
        leverage,
        warmup_bars: warmupBars,
        demo,
      }
      if (selectedStrategy !== CUSTOM_KEY && selectedStrategy) {
        req.strategy = selectedStrategy
      }
      await api.startLive(req)
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
  const adaptedResult: BacktestResult | null =
    perf
      ? livePerformanceToBacktestResult(perf, {
          exchange: activeEngine?.exchange,
          strategy: activeEngine?.strategy,
        })
      : null

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 37px)' }}>
      <div className="flex flex-1 min-h-0">
        {/* ── Left panel: strategy selector + settings ─────────────── */}
        <div
          className="w-80 shrink-0 flex flex-col bg-card border-r border-border"
          style={{ height: '100%' }}
        >
          {/* Header */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between shrink-0">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              Live Trading
            </span>
            {activeEngine && <StatusBadge status={activeEngine.status} />}
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto min-h-0 flex flex-col">
            {/* Strategy selector */}
            <Section title="Strategy">
              <div className="space-y-1">
                <div className="flex flex-col gap-0.5 py-1">
                  <Label>Strategy</Label>
                  <Select
                    className="text-xs h-7"
                    value={selectedStrategy}
                    onChange={(e) => handleStrategyChange(e.target.value)}
                    disabled={!!activeEngine}
                  >
                    <option value="">— Select —</option>
                    <option value={CUSTOM_KEY}>Custom Pine Script</option>
                    {strategies.map((s) => (
                      <option key={s.name} value={s.name}>
                        {s.display_name}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>
            </Section>

            {/* Pine Script editor */}
            <Section title="Pine Script" defaultOpen={!activeEngine}>
              <Textarea
                className="w-full font-mono text-[11px] resize-y"
                spellCheck={false}
                rows={10}
                style={{ minHeight: 100, maxHeight: 300 }}
                value={source}
                onChange={(e) => handleSourceChange(e.target.value)}
                disabled={!!activeEngine}
              />
            </Section>

            {/* Parameters */}
            {pineParams.length > 0 && (
              <Section
                title={`Parameters (${pineParams.length})`}
                defaultOpen={!activeEngine}
              >
                <div className="space-y-0">
                  {pineParams.map((p) => (
                    <div key={p.name} className="flex flex-col gap-0.5 py-1">
                      <Label>{p.title}</Label>
                      <Input
                        type="number"
                        className="text-xs h-7"
                        value={p.value}
                        step={p.step ?? (p.type === 'int' ? 1 : 0.01)}
                        min={p.min}
                        max={p.max}
                        disabled={!!activeEngine}
                        onChange={(e) => {
                          const v =
                            p.type === 'int'
                              ? parseInt(e.target.value)
                              : parseFloat(e.target.value)
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
                  <Label>Exchange</Label>
                  <Select
                    className="text-xs h-7"
                    value={exchange}
                    onChange={(e) => setExchange(e.target.value)}
                    disabled={!!activeEngine}
                  >
                    {exchanges.map((ex) => (
                      <option key={ex.id} value={ex.id}>
                        {ex.name}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label className="text-[10px]">Symbol</Label>
                  <Input
                    className="text-xs h-7"
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                    disabled={!!activeEngine}
                  />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label className="text-[10px]">Timeframe</Label>
                  <Select
                    className="text-xs h-7"
                    value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value)}
                    disabled={!!activeEngine}
                  >
                    {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
                      <option key={tf} value={tf}>
                        {tf}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="flex gap-2">
                  <div className="flex-1 flex flex-col gap-0.5 py-1">
                    <Label className="text-[10px]">Position Size (USDT)</Label>
                    <Input
                      type="number"
                      className="text-xs h-7"
                      value={positionSize}
                      onChange={(e) => setPositionSize(Number(e.target.value))}
                      disabled={!!activeEngine}
                    />
                  </div>
                  <div className="flex-1 flex flex-col gap-0.5 py-1">
                    <Label className="text-[10px]">Leverage</Label>
                    <Input
                      type="number"
                      className="text-xs h-7"
                      value={leverage}
                      min={1}
                      max={125}
                      onChange={(e) => setLeverage(Number(e.target.value))}
                      disabled={!!activeEngine}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label className="text-[10px]">Warmup Bars</Label>
                  <Input
                    type="number"
                    className="text-xs h-7"
                    value={warmupBars}
                    onChange={(e) => setWarmupBars(Number(e.target.value))}
                    disabled={!!activeEngine}
                  />
                </div>
                <div className="flex items-center gap-2 py-1">
                  <Checkbox
                    id="demo-toggle"
                    checked={demo}
                    onCheckedChange={(checked) => setDemo(!!checked)}
                    disabled={!!activeEngine}
                  />
                  <Label htmlFor="demo-toggle" className="text-[10px] cursor-pointer">
                    Demo Mode (Sandbox)
                  </Label>
                </div>
              </div>
            </Section>

            {/* Running engines */}
            {engines.length > 0 && (
              <Section title={`Engines (${engines.length})`}>
                <div className="space-y-1">
                  {engines.map((eng) => (
                    <div
                      key={eng.engine_id}
                      className="flex items-center justify-between py-1 px-1 rounded hover:bg-muted/50"
                    >
                      <div className="flex-1 min-w-0">
                        <div className="text-[11px] text-foreground truncate">
                          {eng.strategy}
                        </div>
                        <div className="text-[9px] text-muted-foreground">
                          {eng.symbol} {eng.timeframe} {eng.demo ? 'DEMO' : 'LIVE'}
                        </div>
                      </div>
                      <StatusBadge status={eng.status} />
                    </div>
                  ))}
                </div>
              </Section>
            )}
          </div>

          {/* Bottom action */}
          <div className="px-3 py-2 border-t border-border shrink-0">
            {startError && (
              <div
                className="text-[10px] text-tv-red mb-1 truncate"
                title={startError}
              >
                {startError}
              </div>
            )}
            {activeEngine ? (
              <Button
                variant="destructive"
                size="sm"
                className="w-full"
                onClick={() => handleStop(activeEngine.engine_id)}
              >
                Stop {activeEngine.strategy}
              </Button>
            ) : (
              <Button
                size="sm"
                className="w-full"
                disabled={starting || !source.trim()}
                onClick={handleStart}
              >
                {starting ? 'Starting...' : 'Start Live Trading'}
              </Button>
            )}
          </div>
        </div>

        {/* ── Right panel: strategy tester / performance ─────────── */}
        <div className="flex-1 flex flex-col min-w-0 bg-background">
          {activeEngine ? (
            <>
              {/* Engine info bar */}
              <div className="px-3 py-2 border-b border-border flex items-center gap-3 shrink-0">
                <StatusBadge status={activeEngine.status} />
                <span className="text-xs text-foreground font-medium">
                  {activeEngine.strategy}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {activeEngine.symbol}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {activeEngine.timeframe}
                </span>
                <span className="text-[10px] text-muted-foreground">
                  {activeEngine.exchange}
                </span>
                {activeEngine.demo && (
                  <Badge variant="warning" className="text-[9px]">
                    DEMO
                  </Badge>
                )}
                {activeEngine.leverage > 1 && (
                  <Badge variant="outline" className="text-[9px]">
                    {activeEngine.leverage}x
                  </Badge>
                )}
                <div className="flex-1" />
                <span
                  className={cn(
                    'w-2 h-2 rounded-full',
                    wsConnected ? 'bg-tv-green animate-pulse' : 'bg-muted-foreground',
                  )}
                />
                <span className="text-[9px] text-muted-foreground">
                  {wsConnected ? 'Connected' : 'Offline'}
                </span>
              </div>

              {/* Strategy Tester content */}
              <div className="flex-1 overflow-auto">
                {adaptedResult && adaptedResult.total_trades > 0 ? (
                  <StrategyTester result={adaptedResult} />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <div className="text-center">
                      <div className="text-muted-foreground text-sm">
                        {activeEngine.status === 'warmup'
                          ? 'Warming up...'
                          : 'Waiting for trades...'}
                      </div>
                      <div className="text-muted-foreground/60 text-xs mt-1">
                        The strategy report will appear once trades are executed.
                      </div>
                      {perf && (
                        <div className="mt-4 text-[10px] text-muted-foreground/80 space-y-0.5">
                          <div>
                            Balance: {perf.current_balance.toLocaleString()} USDT
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </>
          ) : (
            /* No active engine */
            <div className="flex items-center justify-center h-full">
              <div className="text-center max-w-md">
                <div className="text-muted-foreground text-lg mb-2">
                  No Live Engine Running
                </div>
                <div className="text-muted-foreground/60 text-xs leading-relaxed">
                  Select a strategy, configure settings, and click "Start Live
                  Trading" to begin. The strategy report will update in
                  real-time as trades are executed.
                </div>
                {engines.filter(
                  (e) => e.status === 'stopped' || e.status === 'failed',
                ).length > 0 && (
                  <div className="mt-4 text-[10px] text-muted-foreground/50">
                    {
                      engines.filter((e) => e.status === 'stopped').length
                    }{' '}
                    stopped engine(s)
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

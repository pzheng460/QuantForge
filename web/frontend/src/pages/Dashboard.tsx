import { useEffect, useState, useRef, useCallback } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { api, subscribeLivePerformance } from '../api/client'
import { useDashboardStore, CUSTOM_KEY, DEFAULT_PINE } from '../stores/dashboardStore'
import { useShallow } from 'zustand/react/shallow'
import { useCatalog } from '../hooks/useCatalog'
import { useLiveEngines, useStartLive, useStopLive } from '../hooks/use-queries'
import type {
  LiveEngineOut,
  LiveStartRequest,
  BacktestResult,
} from '../types'
import StrategyTester from '../components/StrategyTester'
import TradingChart from '../components/charts/TradingChart'
import { livePerformanceToBacktestResult } from '../utils/liveAdapter'
import type { EquityPoint, TradeRecord } from '../types'
import { liveStartSchema, type LiveStartFormData } from '@/lib/schemas'
import { FormField } from '@/components/ui/form-field'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset, SidebarProvider,
} from '@/components/ui/sidebar'

// ─── Pine parameter parsing ─────────────────────────────────────────────────

interface PineParam {
  name: string; type: 'int' | 'float'; value: number; title: string
  min?: number; max?: number; step?: number
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
    const maxMatch = argsStr.match(/maxval\s*=\s*(-?\d+(?:\.\d+)?)/)
    const stepMatch = argsStr.match(/step\s*=\s*(\d+(?:\.\d+)?)/)
    params.push({
      name, type: ptype, value, title,
      min: minMatch ? parseFloat(minMatch[1]) : undefined,
      max: maxMatch ? parseFloat(maxMatch[1]) : undefined,
      step: stepMatch ? parseFloat(stepMatch[1]) : undefined,
    })
  }
  return params
}

function updatePineParam(source: string, paramName: string, newValue: number): string {
  return source.replace(
    new RegExp(`(${paramName}\\s*=\\s*input\\.(?:int|float)\\()(-?\\d+(?:\\.\\d+)?)`),
    `$1${newValue}`,
  )
}

// ─── Status badge ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const v: Record<string, 'success' | 'warning' | 'secondary' | 'destructive'> = {
    running: 'success', warmup: 'warning', stopped: 'secondary', failed: 'destructive',
  }
  return (
    <Badge variant={v[status] || 'secondary'} className="gap-1 text-[10px]">
      {status === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-tv-green animate-pulse" />}
      {status === 'warmup' && <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />}
      {status.toUpperCase()}
    </Badge>
  )
}

// ─── Live equity chart: subscribes to perf, updates on every WS push ────────

function LiveEquityChart() {
  const perf = useDashboardStore((s) => s.perf)

  // Build equity curve — cached in ref so TradingChart never unmounts
  const curveRef = useRef<EquityPoint[]>([])
  const tradesRef = useRef<TradeRecord[]>([])

  if (perf && perf.total_trades > 0) {
    const initial = perf.initial_balance || 10000
    const curve: EquityPoint[] = [
      { t: perf.start_time || perf.last_update, strategy: initial, bh: initial },
    ]
    let running = initial
    for (const t of perf.trades) {
      running += t.pnl
      curve.push({ t: t.exit_time, strategy: running, bh: initial })
    }
    curve.push({ t: perf.last_update, strategy: perf.current_balance, bh: initial })
    curveRef.current = curve

    tradesRef.current = perf.trades.map((t) => ({
      timestamp: t.entry_time,
      side: (t.side === 'long' ? 'buy' : 'sell') as 'buy' | 'sell',
      price: t.entry_price,
      exit_price: t.exit_price,
      amount: t.amount || 0,
      fee: 0,
      pnl: t.pnl,
      pnl_pct: t.pnl_pct,
      entry_time: t.entry_time,
      exit_time: t.exit_time,
    }))
  }

  // Always render — TradingChart stays mounted, updates via setData
  return <TradingChart equityCurve={curveRef.current} trades={tradesRef.current} />
}

// ─── Info bar: subscribes to perf for live stats ────────────────────────────

function LiveInfoBar({ activeEngine }: { activeEngine: LiveEngineOut }) {
  const perf = useDashboardStore((s) => s.perf)
  const wsConnected = useDashboardStore((s) => s.wsConnected)

  return (
    <div className="px-3 py-2 border-b border-border flex items-center gap-3 shrink-0">
      <StatusBadge status={activeEngine.status} />
      <span className="text-xs text-foreground font-medium">{activeEngine.strategy}</span>
      <span className="text-[10px] text-muted-foreground">{activeEngine.symbol}</span>
      <span className="text-[10px] text-muted-foreground">{activeEngine.timeframe}</span>
      <span className="text-[10px] text-muted-foreground">{activeEngine.exchange}</span>
      {activeEngine.demo && <Badge variant="warning" className="text-[9px]">DEMO</Badge>}
      {activeEngine.leverage > 1 && <Badge variant="outline" className="text-[9px]">{activeEngine.leverage}x</Badge>}
      <div className="flex-1" />
      {perf && (
        <div className="flex items-center gap-3 text-[10px] tabular-nums">
          <span className="text-muted-foreground">
            Balance: <span className="text-foreground font-medium">{perf.current_balance.toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
          </span>
          <span className={perf.total_pnl >= 0 ? 'text-tv-green' : 'text-tv-red'}>
            P&L: {perf.total_pnl >= 0 ? '+' : ''}{perf.total_pnl.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            ({perf.total_return_pct >= 0 ? '+' : ''}{perf.total_return_pct.toFixed(2)}%)
          </span>
          <span className="text-muted-foreground">Trades: {perf.total_trades}</span>
        </div>
      )}
      <span className={cn('w-2 h-2 rounded-full', wsConnected ? 'bg-tv-green animate-pulse' : 'bg-muted-foreground')} />
      <span className="text-[9px] text-muted-foreground">{wsConnected ? 'Live' : 'Offline'}</span>
    </div>
  )
}

// ─── Report area: only re-renders when total_trades changes ─────────────────

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { height, onMouseDown }
}

function LiveReportPanel({ activeEngine }: { activeEngine?: LiveEngineOut }) {
  const tradeCount = useDashboardStore((s) => s.perf?.total_trades ?? 0)
  const setPerf = useDashboardStore((s) => s.setPerf)
  const setWsConnected = useDashboardStore((s) => s.setWsConnected)
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

  // WebSocket subscription — drop stale messages where total_trades regresses
  const highWaterRef = useRef(0)
  useEffect(() => {
    const cleanup = subscribeLivePerformance(
      (msg) => {
        if (msg.total_trades >= highWaterRef.current) {
          highWaterRef.current = msg.total_trades
          setPerf(msg)
        }
        setWsConnected(true)
      },
      () => setWsConnected(false),
    )
    return () => cleanup()
  }, [setPerf, setWsConnected])

  // Only rebuild when tradeCount changes (new trade). Never go back to null.
  const lastGoodResult = useRef<BacktestResult | null>(null)
  const lastBuiltCount = useRef(0)

  if (tradeCount > 0 && tradeCount !== lastBuiltCount.current) {
    const perf = useDashboardStore.getState().perf
    if (perf) {
      lastBuiltCount.current = tradeCount
      lastGoodResult.current = livePerformanceToBacktestResult(perf, {
        exchange: activeEngine?.exchange,
        strategy: activeEngine?.strategy,
      })
    }
  }

  const adaptedResult = lastGoodResult.current

  if (!activeEngine) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center max-w-md">
          <div className="text-muted-foreground text-lg mb-2">No Live Engine Running</div>
          <div className="text-muted-foreground/60 text-xs leading-relaxed">
            Select a strategy, configure settings, and click "Start Live Trading" to begin.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full">
      {/* Info bar — updates every WS push */}
      <LiveInfoBar activeEngine={activeEngine} />

      {/* Chart area — fills remaining space above the bottom panel */}
      <div className="flex-1 min-h-0 bg-background relative">
        <LiveEquityChart />
      </div>

      {/* Resize handle */}
      {adaptedResult && (
        <div
          className="h-1 bg-border cursor-row-resize hover:bg-primary transition-colors shrink-0"
          onMouseDown={onDragStart}
        />
      )}

      {/* Bottom Strategy Tester panel — only re-renders when tradeCount changes */}
      {adaptedResult ? (
        <div className="shrink-0 bg-card border-t border-border overflow-hidden" style={{ height: bottomHeight }}>
          <StrategyTester result={adaptedResult} />
        </div>
      ) : (
        <div className="shrink-0 flex items-center justify-center bg-card border-t border-border" style={{ height: bottomHeight }}>
          <div className="text-center">
            <div className="text-muted-foreground text-sm">
              {activeEngine.status === 'warmup' ? 'Warming up indicators...' : 'Waiting for first trade...'}
            </div>
            <div className="text-muted-foreground/60 text-xs mt-1">
              The report will appear after the first trade closes.
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main Live Trading page ─────────────────────────────────────────────────

export default function DashboardPage() {
  const { strategies, exchanges } = useCatalog()

  // Zustand store — UI state only, no data-fetching state
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
    startError, setStartError,
    initialized, setInitialized,
  } = useDashboardStore(useShallow((s) => ({
    selectedStrategy: s.selectedStrategy, setSelectedStrategy: s.setSelectedStrategy,
    source: s.source, setSource: s.setSource,
    pineParams: s.pineParams, setPineParams: s.setPineParams,
    exchange: s.exchange, setExchange: s.setExchange,
    symbol: s.symbol, setSymbol: s.setSymbol,
    timeframe: s.timeframe, setTimeframe: s.setTimeframe,
    positionSize: s.positionSize, setPositionSize: s.setPositionSize,
    leverage: s.leverage, setLeverage: s.setLeverage,
    warmupBars: s.warmupBars, setWarmupBars: s.setWarmupBars,
    demo: s.demo, setDemo: s.setDemo,
    startError: s.startError, setStartError: s.setStartError,
    initialized: s.initialized, setInitialized: s.setInitialized,
  })))

  // React Query: live engines (replaces manual polling + zustand engines state)
  const { data: engines = [] } = useLiveEngines()
  const startLiveMutation = useStartLive()
  const stopLiveMutation = useStopLive()

  // React Hook Form with Zod validation for settings fields
  const {
    register,
    control,
    handleSubmit,
    formState: { errors: formErrors },
    setValue: setFormValue,
  } = useForm<LiveStartFormData>({
    resolver: zodResolver(liveStartSchema),
    defaultValues: {
      strategy: selectedStrategy,
      exchange,
      symbol,
      timeframe,
      positionSize,
      leverage,
      warmupBars,
      demo,
    },
  })

  const paramUpdateRef = useRef(false)

  const activeEngine = engines.find((e) => e.status === 'running' || e.status === 'warmup')

  useEffect(() => {
    if (!initialized && strategies.length > 0) setInitialized(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleStrategyChange = useCallback((name: string) => {
    setSelectedStrategy(name)
    setFormValue('strategy', name)
    if (name === CUSTOM_KEY) { setSource(DEFAULT_PINE); setPineParams(parsePineParams(DEFAULT_PINE)) }
    else { api.strategySource(name).then(({ source: src }) => { setSource(src); setPineParams(parsePineParams(src)) }) }
  }, [setFormValue])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleSourceChange = useCallback((val: string) => {
    setSource(val)
    if (paramUpdateRef.current) { paramUpdateRef.current = false; return }
    setPineParams(parsePineParams(val))
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev) => updatePineParam(prev, paramName, newValue))
    setPineParams((prev) => prev.map((p) => p.name === paramName ? { ...p, value: newValue } : p))
  }, [])

  const onValidStart = useCallback((data: LiveStartFormData) => {
    // Sync validated form data to Zustand
    setExchange(data.exchange)
    setSymbol(data.symbol)
    setTimeframe(data.timeframe)
    setPositionSize(data.positionSize)
    setLeverage(data.leverage)
    setWarmupBars(data.warmupBars)
    setDemo(data.demo)

    setStartError(null)
    const req: LiveStartRequest = {
      pine_source: source,
      exchange: data.exchange,
      symbol: data.symbol,
      timeframe: data.timeframe,
      position_size_usdt: data.positionSize,
      leverage: data.leverage,
      warmup_bars: data.warmupBars,
      demo: data.demo,
    }
    if (selectedStrategy !== CUSTOM_KEY && selectedStrategy) req.strategy = selectedStrategy
    startLiveMutation.mutate(req, { onError: (e) => setStartError(String(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, selectedStrategy, startLiveMutation])

  const handleStop = useCallback((engineId: string) => {
    stopLiveMutation.mutate(engineId, { onError: (e) => setStartError(String(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopLiveMutation])

  return (
    <SidebarProvider defaultOpen>
        {/* ── Left panel (Sidebar) ────────────────────────────────── */}
        <Sidebar collapsible="none" className="border-r border-border">
          <SidebarHeader className="px-3 py-2 border-b border-border flex-row items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Live Trading</span>
            {activeEngine && <StatusBadge status={activeEngine.status} />}
          </SidebarHeader>

          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Strategy</SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-1">
                  <div className="flex flex-col gap-0.5 py-1">
                    <Label>Strategy</Label>
                    <Select value={selectedStrategy} onValueChange={handleStrategyChange} disabled={!!activeEngine}>
                      <SelectTrigger className="text-xs h-7">
                        <SelectValue placeholder="-- Select --" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value={CUSTOM_KEY}>Custom Pine Script</SelectItem>
                        {strategies.map((s) => <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </SidebarGroupContent>
            </SidebarGroup>

            <SidebarGroup>
              <SidebarGroupLabel>Pine Script</SidebarGroupLabel>
              <SidebarGroupContent>
                <Textarea className="w-full font-mono text-[11px] resize-y" spellCheck={false} rows={10}
                  style={{ minHeight: 100, maxHeight: 300 }} value={source}
                  onChange={(e) => handleSourceChange(e.target.value)} disabled={!!activeEngine} />
              </SidebarGroupContent>
            </SidebarGroup>

            {pineParams.length > 0 && (
              <SidebarGroup>
                <SidebarGroupLabel>{`Parameters (${pineParams.length})`}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <div className="space-y-0">
                    {pineParams.map((p) => (
                      <div key={p.name} className="flex flex-col gap-0.5 py-1">
                        <Label>{p.title}</Label>
                        <Input type="number" className="text-xs h-7" value={p.value}
                          step={p.step ?? (p.type === 'int' ? 1 : 0.01)} min={p.min} max={p.max}
                          disabled={!!activeEngine}
                          onChange={(e) => { const v = p.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value); if (!isNaN(v)) handleParamChange(p.name, v) }} />
                      </div>
                    ))}
                  </div>
                </SidebarGroupContent>
              </SidebarGroup>
            )}

            <SidebarGroup>
              <SidebarGroupLabel>Settings</SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-1">
                  <FormField label="Exchange" error={formErrors.exchange?.message}>
                    <Controller
                      name="exchange"
                      control={control}
                      render={({ field }) => (
                        <Select value={field.value} onValueChange={(v) => { field.onChange(v); setExchange(v) }} disabled={!!activeEngine}>
                          <SelectTrigger className="text-xs h-7">
                            <SelectValue placeholder="Select exchange" />
                          </SelectTrigger>
                          <SelectContent>
                            {exchanges.map((ex) => <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </FormField>
                  <FormField label="Symbol" error={formErrors.symbol?.message}>
                    <Input
                      className="text-xs h-7"
                      disabled={!!activeEngine}
                      {...register('symbol', {
                        onChange: (e) => setSymbol(e.target.value),
                      })}
                    />
                  </FormField>
                  <FormField label="Timeframe" error={formErrors.timeframe?.message}>
                    <Controller
                      name="timeframe"
                      control={control}
                      render={({ field }) => (
                        <Select value={field.value} onValueChange={(v) => { field.onChange(v); setTimeframe(v) }} disabled={!!activeEngine}>
                          <SelectTrigger className="text-xs h-7">
                            <SelectValue placeholder="Select timeframe" />
                          </SelectTrigger>
                          <SelectContent>
                            {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => <SelectItem key={tf} value={tf}>{tf}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </FormField>
                  <div className="flex gap-2">
                    <FormField label="Position Size (USDT)" error={formErrors.positionSize?.message} className="flex-1">
                      <Input type="number" className="text-xs h-7" disabled={!!activeEngine}
                        {...register('positionSize', { valueAsNumber: true, onChange: (e) => setPositionSize(Number(e.target.value)) })}
                      />
                    </FormField>
                    <FormField label="Leverage" error={formErrors.leverage?.message} className="flex-1">
                      <Input type="number" className="text-xs h-7" min={1} max={125} disabled={!!activeEngine}
                        {...register('leverage', { valueAsNumber: true, onChange: (e) => setLeverage(Number(e.target.value)) })}
                      />
                    </FormField>
                  </div>
                  <FormField label="Warmup Bars" error={formErrors.warmupBars?.message}>
                    <Input type="number" className="text-xs h-7" disabled={!!activeEngine}
                      {...register('warmupBars', { valueAsNumber: true, onChange: (e) => setWarmupBars(Number(e.target.value)) })}
                    />
                  </FormField>
                  <Controller
                    name="demo"
                    control={control}
                    render={({ field }) => (
                      <div className="flex items-center gap-2 py-1">
                        <Checkbox id="demo-toggle" checked={field.value}
                          onCheckedChange={(c) => { const v = !!c; field.onChange(v); setDemo(v) }}
                          disabled={!!activeEngine} />
                        <Label htmlFor="demo-toggle" className="text-[10px] cursor-pointer">Demo Mode (Sandbox)</Label>
                      </div>
                    )}
                  />
                </div>
              </SidebarGroupContent>
            </SidebarGroup>

            {engines.length > 0 && (
              <SidebarGroup>
                <SidebarGroupLabel>{`Engines (${engines.length})`}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <div className="space-y-1">
                    {engines.map((eng) => (
                      <div key={eng.engine_id} className="flex items-center justify-between py-1 px-1 rounded hover:bg-muted/50">
                        <div className="flex-1 min-w-0">
                          <div className="text-[11px] text-foreground truncate">{eng.strategy}</div>
                          <div className="text-[9px] text-muted-foreground">{eng.symbol} {eng.timeframe} {eng.demo ? 'DEMO' : 'LIVE'}</div>
                        </div>
                        <StatusBadge status={eng.status} />
                      </div>
                    ))}
                  </div>
                </SidebarGroupContent>
              </SidebarGroup>
            )}
          </SidebarContent>

          <SidebarFooter className="px-3 py-2 border-t border-border">
            {(startError || startLiveMutation.error) && (
              <div className="text-[10px] text-tv-red mb-1 truncate" title={startError || String(startLiveMutation.error)}>
                {startError || String(startLiveMutation.error)}
              </div>
            )}
            {activeEngine ? (
              <Button variant="destructive" size="sm" className="w-full" onClick={() => handleStop(activeEngine.engine_id)}>
                Stop {activeEngine.strategy}
              </Button>
            ) : (
              <Button size="sm" className="w-full" disabled={startLiveMutation.isPending || !source.trim()} onClick={handleSubmit(onValidStart)}>
                {startLiveMutation.isPending ? 'Starting...' : 'Start Live Trading'}
              </Button>
            )}
          </SidebarFooter>
        </Sidebar>

        {/* ── Right panel — isolated, WS updates don't touch the left panel ── */}
        <SidebarInset className="flex flex-col min-w-0">
          <LiveReportPanel activeEngine={activeEngine} />
        </SidebarInset>
    </SidebarProvider>
  )
}

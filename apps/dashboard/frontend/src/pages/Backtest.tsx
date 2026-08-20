import { useEffect, useState, useRef, useCallback } from 'react'
import { Activity, Loader2, Play, Square } from 'lucide-react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { useBacktestStore } from '../stores/backtestStore'
import { useCatalog } from '../hooks/useCatalog'
import { useBacktestStatus, useRunBacktest, useCancelBacktest } from '../hooks/use-queries'
import type { BacktestRequest, StrategySchema, Exchange } from '../types'
import TradingChart from '../components/charts/TradingChart'
import StrategyTester from '../components/StrategyTester'
import { backtestSchema, type BacktestFormData } from '@/lib/schemas'
import { FormField } from '@/components/ui/form-field'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
} from '@/components/ui/sidebar'
import { ResizableSidebarShell } from '@/components/ResizableSidebarShell'
import { ResizeHandle } from '@/components/ResizeHandle'

// ─── Resizable bottom panel ──────────────────────────────────────────────────

function useResizablePanel(defaultHeight: number) {
  const [height, setHeight] = useState(defaultHeight)
  const heightRef = useRef(defaultHeight)
  heightRef.current = height

  // Imperative drag — attach document-level capture-phase listeners on
  // pointerdown so we beat any sibling (lightweight-charts crosshair) to
  // the events, regardless of React re-render timing.
  const onPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const startY = e.clientY
    const startH = heightRef.current
    const pointerId = e.pointerId

    function onMove(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      const delta = startY - ev.clientY
      setHeight(Math.max(80, Math.min(2000, startH + delta)))
    }
    function onUp(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      cleanup()
    }
    function onCancel(ev: PointerEvent) {
      if (ev.pointerId !== pointerId) return
      cleanup()
    }
    function cleanup() {
      document.removeEventListener('pointermove', onMove, true)
      document.removeEventListener('pointerup', onUp, true)
      document.removeEventListener('pointercancel', onCancel, true)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('pointermove', onMove, true)
    document.addEventListener('pointerup', onUp, true)
    document.addEventListener('pointercancel', onCancel, true)
    document.body.style.cursor = 'row-resize'
    document.body.style.userSelect = 'none'
  }, [])

  return { height, onPointerDown }
}

// ─── Main Backtest page ─────────────────────────────────────────────────────

export default function BacktestPage() {
  const { strategies, exchanges } = useCatalog()

  // Zustand store — UI state only (persists across tab switches)
  const {
    selectedStrategy, setSelectedStrategy,
    strategyParams, setStrategyParams,
    exchange, setExchange,
    symbol, setSymbol,
    timeframe, setTimeframe,
    startDate, setStartDate,
    endDate, setEndDate,
    warmupBars, setWarmupBars,
    positionSizeUsdt, setPositionSizeUsdt,
    jobId, setJobId,
    status, setStatus,
    result, setResult,
    error, setError,
    loading, setLoading,
    initialized, setInitialized,
  } = useBacktestStore()

  // React Query: poll backtest status when a job is running
  const isPolling = !!jobId && status !== 'completed' && status !== 'failed' && status !== 'cancelled'
  const backtestQuery = useBacktestStatus(jobId, isPolling)
  const runBacktestMutation = useRunBacktest()
  const cancelBacktestMutation = useCancelBacktest()

  // Sync React Query polling results into Zustand for UI display
  useEffect(() => {
    if (!backtestQuery.data) return
    const job = backtestQuery.data
    setStatus(job.status)
    if (job.status === 'completed' && job.result) {
      setResult(job.result)
      setLoading(false)
    } else if (job.status === 'failed') {
      setError(job.error ?? 'Unknown error')
      setLoading(false)
    }
  }, [backtestQuery.data, setStatus, setResult, setLoading, setError])

  // React Hook Form with Zod validation for settings fields
  const {
    register,
    control,
    handleSubmit,
    formState: { errors: formErrors },
  } = useForm<BacktestFormData>({
    resolver: zodResolver(backtestSchema),
    defaultValues: {
      exchange,
      symbol,
      timeframe,
      startDate,
      endDate,
      warmupBars,
      positionSizeUsdt,
    },
  })

  // Resizable bottom panel
  const { height: bottomHeight, onPointerDown: onDragStart } = useResizablePanel(280)

  // Set default strategy on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      // Don't auto-select -- start with empty state
      setInitialized(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  const handleStrategyChange = useCallback((name: string) => {
    setSelectedStrategy(name)
    const schema = strategies.find((item) => item.name === name)
    setStrategyParams((schema?.config_fields ?? []).map((field) => ({
      name: field.name,
      type: field.type === 'int' ? 'int' : 'float',
      value: Number(field.default ?? 0),
      title: field.label || field.name,
      min: field.min,
      max: field.max,
      step: field.step,
    })))
    // The options strategy backtest only supports single-name TSLA/NVDA bars:
    // prefill the ticker so the managed model isn't fed an arbitrary symbol.
    if (name === 'tsla_nvda_options' && !symbol) {
      setSymbol('NVDA')
    }
  }, [setSelectedStrategy, setStrategyParams, strategies, symbol, setSymbol])

  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    setStrategyParams((prev) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p)),
    )
  }, [setStrategyParams])

  // Submit backtest via React Query mutation — triggered after Zod validation
  const onValidSubmit = useCallback((data: BacktestFormData) => {
    // Sync validated form data back to Zustand
    setExchange(data.exchange)
    setSymbol(data.symbol ?? '')
    setTimeframe(data.timeframe)
    setStartDate(data.startDate)
    setEndDate(data.endDate)
    setWarmupBars(data.warmupBars)
    setPositionSizeUsdt(data.positionSizeUsdt)

    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')
    const req: BacktestRequest = {
      strategy: selectedStrategy,
      exchange: data.exchange,
      symbol: data.symbol,
      timeframe: data.timeframe,
      start_date: data.startDate,
      end_date: data.endDate,
      warmup_bars: data.warmupBars,
      position_size_usdt: data.positionSizeUsdt,
      config_override: Object.fromEntries(
        strategyParams.map((param) => [param.name, param.value]),
      ),
    }
    runBacktestMutation.mutate(req, {
      onSuccess: (job) => setJobId(job.job_id),
      onError: (e) => { setError(String(e)); setLoading(false) },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStrategy, strategyParams])

  const handleCancel = useCallback(() => {
    if (!jobId) return
    cancelBacktestMutation.mutate(jobId, {
      onSuccess: () => { setStatus('cancelled'); setLoading(false) },
    })
  }, [jobId, cancelBacktestMutation, setStatus, setLoading])

  const selectedExchange = exchanges.find((ex) => ex.id === exchange)

  return (
    <ResizableSidebarShell storageKey="backtest" defaultWidth={320}>
      <Sidebar collapsible="none">
        <SidebarHeader className="border-b border-sidebar-border px-3 py-2">
          <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            Backtest
          </span>
        </SidebarHeader>

        <SidebarContent>
          {/* Strategy selector */}
          <SidebarGroup>
            <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">Strategy</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="space-y-1 px-2">
                <div className="flex flex-col gap-1">
                  <Label>Strategy</Label>
                  <Select value={selectedStrategy} onValueChange={handleStrategyChange}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="-- Select --" />
                    </SelectTrigger>
                    <SelectContent>
                      {strategies.map((s: StrategySchema) => (
                        <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </SidebarGroupContent>
          </SidebarGroup>

          {strategyParams.length > 0 && (
            <SidebarGroup>
              <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">
                {`Parameters (${strategyParams.length})`}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-0 px-2">
                  {strategyParams.map((p) => (
                    <div key={p.name} className="flex flex-col gap-0.5 py-1">
                      <Label>{p.title}</Label>
                      <Input
                        type="number"
                        className="text-xs h-8"
                        value={p.value}
                        step={p.step ?? (p.type === 'int' ? 1 : 0.01)}
                        min={p.min ?? undefined}
                        max={p.max ?? undefined}
                        onChange={(e) => {
                          const v = p.type === 'int'
                            ? parseInt(e.target.value)
                            : parseFloat(e.target.value)
                          if (!isNaN(v)) handleParamChange(p.name, v)
                        }}
                      />
                    </div>
                  ))}
                </div>
              </SidebarGroupContent>
            </SidebarGroup>
          )}

          {/* Backtest settings — validated by react-hook-form + zod */}
          <SidebarGroup>
            <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">Settings</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="space-y-1 px-2">
                <FormField label="Exchange" error={formErrors.exchange?.message}>
                  <Controller
                    name="exchange"
                    control={control}
                    render={({ field }) => (
                      <Select value={field.value} onValueChange={(v) => { field.onChange(v); setExchange(v) }}>
                        <SelectTrigger className="text-xs h-8">
                          <SelectValue placeholder="Select exchange" />
                        </SelectTrigger>
                        <SelectContent>
                          {exchanges.filter((ex: Exchange) => ex.supports_backtest !== false).map((ex: Exchange) => (
                            <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </FormField>

                <FormField
                  label={`Symbol (${selectedExchange?.default_symbol ?? '\u2026'})`}
                  error={formErrors.symbol?.message}
                >
                  <Input
                    type="text"
                    className="text-xs h-8"
                    placeholder={selectedExchange?.default_symbol ?? ''}
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
                      <Select value={field.value} onValueChange={(v) => { field.onChange(v); setTimeframe(v) }}>
                        <SelectTrigger className="text-xs h-8">
                          <SelectValue placeholder="Select timeframe" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="1m">1m</SelectItem>
                          <SelectItem value="5m">5m</SelectItem>
                          <SelectItem value="15m">15m</SelectItem>
                          <SelectItem value="1h">1h</SelectItem>
                          <SelectItem value="4h">4h</SelectItem>
                          <SelectItem value="1d">1d</SelectItem>
                        </SelectContent>
                      </Select>
                    )}
                  />
                </FormField>

                <div className="grid grid-cols-2 gap-2">
                  <FormField label="Start Date" error={formErrors.startDate?.message}>
                    <Input
                      type="date"
                      className="text-xs h-8"
                      {...register('startDate', {
                        onChange: (e) => setStartDate(e.target.value),
                      })}
                    />
                  </FormField>
                  <FormField label="End Date" error={formErrors.endDate?.message}>
                    <Input
                      type="date"
                      className="text-xs h-8"
                      {...register('endDate', {
                        onChange: (e) => setEndDate(e.target.value),
                      })}
                    />
                  </FormField>
                </div>

                <FormField label="Warmup Bars" error={formErrors.warmupBars?.message}>
                  <Input
                    type="number"
                    className="text-xs h-8"
                    min={0}
                    max={10000}
                    {...register('warmupBars', {
                      valueAsNumber: true,
                      onChange: (e) => setWarmupBars(Number(e.target.value)),
                    })}
                  />
                </FormField>

                <FormField
                  label="Position Size (USDT, optional)"
                  error={formErrors.positionSizeUsdt?.message}
                >
                  <Input
                    type="number"
                    className="text-xs h-8"
                    min={0}
                    step="any"
                    placeholder="use strategy default"
                    {...register('positionSizeUsdt', {
                      setValueAs: (v) =>
                        v === '' || v === undefined || v === null
                          ? undefined
                          : Number(v),
                      onChange: (e) =>
                        setPositionSizeUsdt(
                          e.target.value === ''
                            ? undefined
                            : Number(e.target.value),
                        ),
                    })}
                  />
                </FormField>
              </div>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter className="border-t border-sidebar-border">
          {(status === 'pending' || status === 'running') && (
            <div
              className={cn(
                'text-xs text-center capitalize',
                status === 'running'
                  ? 'text-primary animate-pulse'
                  : 'text-muted-foreground',
              )}
            >
              {status === 'running' ? (
                <span className="inline-flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Running...
                </span>
              ) : (
                status
              )}
            </div>
          )}
          {status === 'pending' || status === 'running' ? (
            <Button
              variant="destructive"
              size="sm"
              className="w-full"
              onClick={handleCancel}
            >
              <Square className="mr-1.5 h-3 w-3" />
              Cancel
            </Button>
          ) : (
            <Button
              size="sm"
              className="w-full"
              onClick={handleSubmit(onValidSubmit)}
              disabled={loading || !selectedStrategy}
            >
              {loading ? (
                <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              ) : (
                <Play className="mr-1.5 h-3 w-3" />
              )}
              {loading ? 'Submitting...' : 'Run Backtest'}
            </Button>
          )}
        </SidebarFooter>
      </Sidebar>

      <SidebarInset className="flex flex-col min-w-0">
        {/* Chart area */}
        <div className="flex-1 min-h-0 bg-background relative overflow-hidden">
          {result ? (
            <TradingChart
              equityCurve={result.equity_curve}
              trades={result.trades}
              height={undefined}
            />
          ) : (
            <div className="flex items-center justify-center h-full">
              {loading ? (
                <div className="flex flex-col items-center gap-3 text-muted-foreground">
                  <Loader2 className="h-8 w-8 animate-spin text-primary" />
                  <span className="text-sm capitalize">{status || 'Loading...'}</span>
                </div>
              ) : error ? (
                <div className="max-w-lg px-6">
                  <p className="text-sm font-medium text-tv-red mb-2">Backtest failed</p>
                  <pre className="text-xs text-muted-foreground whitespace-pre-wrap overflow-auto max-h-48 bg-card border border-border rounded-sm p-3">
                    {error}
                  </pre>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-3 text-muted-foreground">
                  <Activity className="h-12 w-12 stroke-1" />
                  <span className="text-sm">Select a Python strategy, adjust parameters, then run the backtest</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Resize handle — chart / results-panel split */}
        {result && <ResizeHandle orientation="vertical" onPointerDown={onDragStart} />}

        {/* Bottom results panel */}
        {result && (
          <div
            className="shrink-0 bg-card border-t border-border overflow-hidden"
            style={{ height: bottomHeight }}
          >
            <StrategyTester result={result} />
          </div>
        )}
      </SidebarInset>
    </ResizableSidebarShell>
  )
}

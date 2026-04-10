import { useEffect, useState, useRef, useCallback } from 'react'
import { Activity, Loader2, Play, Square } from 'lucide-react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { api } from '../api/client'
import { useBacktestStore, CUSTOM_KEY, DEFAULT_PINE } from '../stores/backtestStore'
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
import { Textarea } from '@/components/ui/textarea'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
} from '@/components/ui/sidebar'

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
  let m
  while ((m = re.exec(source))) {
    const [, name, type, argsStr] = m
    const defMatch = argsStr.match(/^(-?\d+\.?\d*)/)
    if (!defMatch) continue
    const ptype = type as 'int' | 'float'
    const value = ptype === 'int' ? parseInt(defMatch[1]) : parseFloat(defMatch[1])
    const titleMatch = argsStr.match(/title\s*=\s*"([^"]*)"/)
    const title = titleMatch ? titleMatch[1] : name
    const minMatch = argsStr.match(/minval\s*=\s*(-?\d+\.?\d*)/)
    const min = minMatch ? parseFloat(minMatch[1]) : undefined
    const maxMatch = argsStr.match(/maxval\s*=\s*(-?\d+\.?\d*)/)
    const max = maxMatch ? parseFloat(maxMatch[1]) : undefined
    const stepMatch = argsStr.match(/step\s*=\s*(\d+\.?\d*)/)
    const step = stepMatch ? parseFloat(stepMatch[1]) : undefined
    params.push({ name, type: ptype, value, title, min, max, step })
  }
  return params
}

function updatePineParam(source: string, paramName: string, newValue: number): string {
  const re = new RegExp(
    `(${paramName}\\s*=\\s*input\\.(?:int|float)\\()(-?\\d+\\.?\\d*)`,
  )
  return source.replace(re, `$1${newValue}`)
}


// ─── Main Backtest page ─────────────────────────────────────────────────────

export default function BacktestPage() {
  const { strategies, exchanges } = useCatalog()

  // Zustand store — UI state only (persists across tab switches)
  const {
    selectedStrategy, setSelectedStrategy,
    source, setSource,
    pineParams, setPineParams,
    exchange, setExchange,
    symbol, setSymbol,
    timeframe, setTimeframe,
    startDate, setStartDate,
    endDate, setEndDate,
    warmupDays, setWarmupDays,
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
      warmupDays,
    },
  })

  // Resizable bottom panel
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

  // Track whether source change is from param update (skip re-parse)
  const paramUpdateRef = useRef(false)

  // Set default strategy on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      // Don't auto-select -- start with empty state
      setInitialized(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  // When selected strategy changes, fetch its source
  // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // When source text changes directly, re-parse params
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleSourceChange = useCallback((newSource: string) => {
    setSource(newSource)
    if (paramUpdateRef.current) {
      paramUpdateRef.current = false
      return
    }
    setPineParams(parsePineParams(newSource))
  }, [])

  // When a param value is changed, update source text
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev: string) => updatePineParam(prev, paramName, newValue))
    setPineParams((prev: PineParam[]) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p)),
    )
  }, [])

  // Submit backtest via React Query mutation — triggered after Zod validation
  const onValidSubmit = useCallback((data: BacktestFormData) => {
    // Sync validated form data back to Zustand
    setExchange(data.exchange)
    setSymbol(data.symbol ?? '')
    setTimeframe(data.timeframe)
    setStartDate(data.startDate)
    setEndDate(data.endDate)
    setWarmupDays(data.warmupDays)

    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')
    const req: BacktestRequest = {
      pine_source: source,
      exchange: data.exchange,
      symbol: data.symbol,
      timeframe: data.timeframe,
      start_date: data.startDate,
      end_date: data.endDate,
      warmup_days: data.warmupDays,
    }
    runBacktestMutation.mutate(req, {
      onSuccess: (job) => setJobId(job.job_id),
      onError: (e) => { setError(String(e)); setLoading(false) },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source])

  const handleCancel = useCallback(() => {
    if (!jobId) return
    cancelBacktestMutation.mutate(jobId, {
      onSuccess: () => { setStatus('cancelled'); setLoading(false) },
    })
  }, [jobId, cancelBacktestMutation, setStatus, setLoading])

  const selectedExchange = exchanges.find((ex) => ex.id === exchange)

  return (
    <SidebarProvider
      defaultOpen
      className="h-full min-h-0"
      style={{ '--sidebar-width': '20rem' } as React.CSSProperties}
    >
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
                      <SelectItem value={CUSTOM_KEY}>Custom Script</SelectItem>
                      {strategies.map((s: StrategySchema) => (
                        <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </SidebarGroupContent>
          </SidebarGroup>

          {/* Pine Script editor */}
          <SidebarGroup>
            <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">Pine Script</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="px-2">
                <Textarea
                  value={source}
                  onChange={(e) => handleSourceChange(e.target.value)}
                  className="text-[11px] font-mono resize-y"
                  spellCheck={false}
                  placeholder="Enter Pine Script code..."
                  rows={12}
                  style={{ minHeight: 120, maxHeight: 400 }}
                />
              </div>
            </SidebarGroupContent>
          </SidebarGroup>

          {/* Parameters (parsed from Pine source) */}
          {pineParams.length > 0 && (
            <SidebarGroup>
              <SidebarGroupLabel className="text-[10px] uppercase tracking-wider">
                {`Parameters (${pineParams.length})`}
              </SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-0 px-2">
                  {pineParams.map((p) => (
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
                          {exchanges.map((ex: Exchange) => (
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

                <FormField label="Warmup Days" error={formErrors.warmupDays?.message}>
                  <Input
                    type="number"
                    className="text-xs h-8"
                    min={0}
                    max={365}
                    {...register('warmupDays', {
                      valueAsNumber: true,
                      onChange: (e) => setWarmupDays(Number(e.target.value)),
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
              disabled={loading || !source.trim()}
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
        <div className="flex-1 min-h-0 bg-background relative">
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
                  <span className="text-sm">Select a strategy or write Pine Script, then click Run Backtest</span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Resize handle */}
        {result && (
          <div
            className="h-1 bg-border cursor-row-resize hover:bg-primary transition-colors"
            onMouseDown={onDragStart}
          />
        )}

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
    </SidebarProvider>
  )
}

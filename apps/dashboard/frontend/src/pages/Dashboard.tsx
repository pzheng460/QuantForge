import { useEffect, useState, useCallback } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { api } from '../api/client'
import { useDashboardStore } from '../stores/dashboardStore'
import { useShallow } from 'zustand/react/shallow'
import { useCatalog } from '../hooks/useCatalog'
import { useLiveEngines, useStartLive, useStopLive, useDeleteLive } from '../hooks/use-queries'
import type {
  LiveEngineOut,
  LiveStartRequest,
} from '../types'
import { SchwabConnection } from '../components/SchwabConnection'
import { liveStartSchema, type LiveStartFormData } from '@/lib/schemas'
import { FormField } from '@/components/ui/form-field'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset,
} from '@/components/ui/sidebar'
import { ResizableSidebarShell } from '@/components/ResizableSidebarShell'

// ─── Status badge ───────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const v: Record<string, 'success' | 'warning' | 'secondary' | 'destructive'> = {
    running: 'success', warmup: 'warning', restarting: 'warning', stopped: 'secondary', failed: 'destructive',
  }
  return (
    <Badge variant={v[status] || 'secondary'} className="gap-1 text-[10px]">
      {status === 'running' && <span className="w-1.5 h-1.5 rounded-full bg-tv-green animate-pulse" />}
      {status === 'warmup' && <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse" />}
      {status.toUpperCase()}
    </Badge>
  )
}

function OptionsAnalysisPanel({ ticker }: { ticker: string }) {
  const [earningsDate, setEarningsDate] = useState('')
  const [coreShares, setCoreShares] = useState(0)
  const [report, setReport] = useState<{
    action: string
    reasons: string[]
    contract_symbol?: string
    contracts: number
    limit_price?: number
  } | null>(null)
  const [error, setError] = useState<string | null>(null)

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Options Daily Analysis</SidebarGroupLabel>
      <SidebarGroupContent>
        <div className="space-y-2">
          <div>
            <Label>Confirmed Earnings Date</Label>
            <Input
              type="date"
              value={earningsDate}
              onChange={(event) => setEarningsDate(event.target.value)}
            />
          </div>
          <div>
            <Label>Minimum Core Shares</Label>
            <Input
              type="number"
              min={0}
              value={coreShares}
              onChange={(event) => setCoreShares(Number(event.target.value))}
            />
          </div>
          <Button
            type="button"
            size="sm"
            className="w-full"
            onClick={() => {
              api.analyzeSchwabOptions({
                ticker,
                as_of: new Date().toISOString().slice(0, 10),
                minimum_core_shares: coreShares,
                maximum_covered_ratio: 0.5,
                trend_state: '横盘',
                earnings_date: earningsDate || null,
                earnings_confirmed: Boolean(earningsDate),
              })
                .then((value) => {
                  setReport(value.report)
                  setError(null)
                })
                .catch((reason) => setError(String(reason)))
            }}
          >
            Analyze Live Chain
          </Button>
          {report && (
            <div className="rounded border border-border p-2 text-[10px]">
              <div className="font-semibold">{report.action}</div>
              <div>{report.reasons.join('；')}</div>
              {report.contract_symbol && (
                <div className="font-mono">
                  {report.contract_symbol} × {report.contracts}
                  {report.limit_price != null ? ` @ ${report.limit_price}` : ''}
                </div>
              )}
            </div>
          )}
          {error && <div className="text-[10px] text-destructive">{error}</div>}
        </div>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}

// ─── Live report: real engine-registry status only ──────────────────────────
// Per-trade live performance telemetry was removed along with its only writer
// (see apps/dashboard/backend/routers/live.py) rather than fabricating P&L
// from unconfirmed engine orders. This panel shows the truthful engine state
// from /live/engines (refreshed by useLiveEngines every 5s).

function LiveStatusPanel({ activeEngine }: { activeEngine?: LiveEngineOut }) {
  if (!activeEngine) {
    return (
      <div className="flex items-center justify-center h-full px-6">
        <div className="text-center max-w-md">
          <div className="text-muted-foreground text-lg mb-2">No Live Engine Running</div>
          <div className="text-muted-foreground/60 text-xs leading-relaxed">
            Select a strategy, configure settings, and click "Start Live Trading" to begin.
          </div>
        </div>
      </div>
    )
  }

  const statusCopy: Record<string, string> = {
    running: 'The engine is live and processing bars; orders are submitted automatically after hard risk checks.',
    warmup: 'Warming up indicators from historical bars before trading decisions start.',
    restarting: 'The engine loop exited and the watchdog is restarting it with backoff.',
    stopped: 'The engine was stopped by an operator.',
    failed: 'The engine failed and is awaiting manual intervention.',
  }

  return (
    <div className="flex flex-col h-full">
      {/* Info bar — engine registry data */}
      <div className="px-3 py-2 border-b border-border flex items-center gap-3 shrink-0">
        <StatusBadge status={activeEngine.status} />
        <span className="text-xs text-foreground font-medium">{activeEngine.strategy}</span>
        <span className="text-[10px] text-muted-foreground">{activeEngine.symbol}</span>
        <span className="text-[10px] text-muted-foreground">{activeEngine.timeframe}</span>
        <span className="text-[10px] text-muted-foreground">{activeEngine.exchange}</span>
        {activeEngine.demo && <Badge variant="warning" className="text-[9px]">DEMO</Badge>}
        {activeEngine.leverage > 1 && <Badge variant="outline" className="text-[9px]">{activeEngine.leverage}x</Badge>}
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto bg-background">
        <div className="p-6 max-w-xl mx-auto">
          <div className="rounded border border-border p-4 space-y-3">
            <div className="text-xs text-foreground font-semibold">Engine Status</div>
            <div className="text-[11px] text-muted-foreground leading-relaxed">
              {statusCopy[activeEngine.status] ?? activeEngine.status}
            </div>
            {activeEngine.error && (
              <div className="rounded bg-destructive/10 border border-destructive/40 px-2 py-1.5 text-[10.5px] text-destructive font-mono break-words">
                {activeEngine.error}
              </div>
            )}
            <div className="text-[10px] text-muted-foreground/70 pt-2 border-t border-border">
              engine {activeEngine.engine_id}
              {activeEngine.stopped_at
                ? <> · stopped {new Date(activeEngine.stopped_at).toLocaleString()}</>
                : <> · started {new Date(activeEngine.created_at).toLocaleString()}</>}
            </div>
            <div className="text-[10px] text-muted-foreground/60">
              Per-trade equity telemetry is not wired yet — this panel shows live engine status only.
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Main Live Trading page ─────────────────────────────────────────────────

export default function DashboardPage() {
  const { strategies, exchanges } = useCatalog()
  const [globalRiskHalted, setGlobalRiskHalted] = useState(false)
  const [globalRiskError, setGlobalRiskError] = useState<string | null>(null)

  // Zustand store — UI state only, no data-fetching state
  const {
    selectedStrategy, setSelectedStrategy,
    strategyParams, setStrategyParams,
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
    strategyParams: s.strategyParams, setStrategyParams: s.setStrategyParams,
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
  const deleteLiveMutation = useDeleteLive()

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

  const activeEngine = engines.find((e) => e.status === 'running' || e.status === 'warmup' || e.status === 'restarting')

  useEffect(() => {
    if (!initialized && strategies.length > 0) setInitialized(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  useEffect(() => {
    api.globalRisk()
      .then((state) => setGlobalRiskHalted(state.halted))
      .catch((error) => setGlobalRiskError(String(error)))
  }, [])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleStrategyChange = useCallback((name: string) => {
    setSelectedStrategy(name)
    setFormValue('strategy', name)
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
  }, [setFormValue, setSelectedStrategy, setStrategyParams, strategies])

  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    setStrategyParams((prev) => prev.map((p) => p.name === paramName ? { ...p, value: newValue } : p))
  }, [setStrategyParams])

  const submitLiveReq = useCallback((req: LiveStartRequest) => {
    startLiveMutation.mutate(req, { onError: (e) => setStartError(String(e)) })
  }, [startLiveMutation])

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
      strategy: selectedStrategy,
      exchange: data.exchange,
      symbol: data.symbol,
      timeframe: data.timeframe,
      position_size_usdt: data.positionSize,
      leverage: data.leverage,
      warmup_bars: data.warmupBars,
      demo: data.demo,
      config_override: Object.fromEntries(
        strategyParams.map((param) => [param.name, param.value]),
      ),
    }
    submitLiveReq(req)
  }, [selectedStrategy, strategyParams, submitLiveReq, setDemo, setExchange, setLeverage, setPositionSize, setStartError, setSymbol, setTimeframe, setWarmupBars])

  const handleStop = useCallback((engineId: string) => {
    stopLiveMutation.mutate(engineId, { onError: (e) => setStartError(String(e)) })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopLiveMutation])

  return (
    <ResizableSidebarShell storageKey="dashboard">
        {/* ── Left panel (Sidebar) ────────────────────────────────── */}
        <Sidebar collapsible="none" className="border-r border-border">
          <SidebarHeader className="px-3 py-2 border-b border-border flex-row items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Live Trading</span>
            <div className="flex items-center gap-2">
              {globalRiskHalted && <Badge variant="destructive">HALTED</Badge>}
              {activeEngine && <StatusBadge status={activeEngine.status} />}
            </div>
          </SidebarHeader>

          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Global Risk</SidebarGroupLabel>
              <SidebarGroupContent>
                <Button
                  type="button"
                  size="sm"
                  className="w-full"
                  variant={globalRiskHalted ? 'default' : 'destructive'}
                  onClick={() => {
                    const next = !globalRiskHalted
                    api.setGlobalRisk(
                      next,
                      next ? 'Dashboard emergency stop' : 'Operator resumed trading',
                    )
                      .then((state) => {
                        setGlobalRiskHalted(state.halted)
                        setGlobalRiskError(null)
                      })
                      .catch((error) => setGlobalRiskError(String(error)))
                  }}
                >
                  {globalRiskHalted ? 'Resume Global Trading' : 'Emergency Stop All Engines'}
                </Button>
                {globalRiskError && (
                  <div className="mt-1 text-[10px] text-destructive">{globalRiskError}</div>
                )}
              </SidebarGroupContent>
            </SidebarGroup>
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
                        {strategies.map((s) => <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>)}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
              </SidebarGroupContent>
            </SidebarGroup>

            {strategyParams.length > 0 && (
              <SidebarGroup>
                <SidebarGroupLabel>{`Parameters (${strategyParams.length})`}</SidebarGroupLabel>
                <SidebarGroupContent>
                  <div className="space-y-0">
                    {strategyParams.map((p) => (
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

            {exchange === 'schwab' && selectedStrategy === 'tsla_nvda_options' && (
              <OptionsAnalysisPanel ticker={symbol || 'TSLA'} />
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
                  {exchange === 'schwab' && <SchwabConnection />}
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
                      <>
                        <div className="flex items-center gap-2 py-1">
                          <Checkbox id="demo-toggle" checked={field.value}
                            onCheckedChange={(c) => { const v = !!c; field.onChange(v); setDemo(v) }}
                            disabled={!!activeEngine} />
                          <Label htmlFor="demo-toggle" className="text-[10px] cursor-pointer">Demo Mode (Sandbox)</Label>
                        </div>
                        {!field.value && (
                          <div className="mt-1 rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-[10.5px] text-destructive leading-snug">
                            <span className="font-semibold">⚠ LIVE mode</span> — Start will place real orders.
                            Orders are submitted automatically after hard risk checks.
                          </div>
                        )}
                      </>
                    )}
                  />
                </div>
              </SidebarGroupContent>
            </SidebarGroup>

            {engines.length > 0 && (() => {
              const active = engines.filter((e) => e.status === 'warmup' || e.status === 'running' || e.status === 'restarting')
              const archived = engines.filter((e) => e.status === 'stopped' || e.status === 'failed')
              return (
                <>
                  {active.length > 0 && (
                    <SidebarGroup>
                      <SidebarGroupLabel>{`Active (${active.length})`}</SidebarGroupLabel>
                      <SidebarGroupContent>
                        <div className="space-y-1">
                          {active.map((eng) => (
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
                  {archived.length > 0 && (
                    <SidebarGroup>
                      <SidebarGroupLabel>{`History (${archived.length})`}</SidebarGroupLabel>
                      <SidebarGroupContent>
                        <div className="space-y-1">
                          {archived.map((eng) => (
                            <div key={eng.engine_id} className="group flex items-center justify-between py-1 px-1 rounded hover:bg-muted/50">
                              <div className="flex-1 min-w-0">
                                <div className="text-[11px] text-muted-foreground truncate">{eng.strategy}</div>
                                <div className="text-[9px] text-muted-foreground/70">{eng.symbol} {eng.timeframe} {eng.demo ? 'DEMO' : 'LIVE'}</div>
                              </div>
                              <div className="flex items-center gap-1">
                                <StatusBadge status={eng.status} />
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (window.confirm(`Delete ${eng.strategy} (${eng.engine_id.slice(0, 8)}) from history?`)) {
                                      deleteLiveMutation.mutate(eng.engine_id)
                                    }
                                  }}
                                  title="Delete from history"
                                  className="opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-destructive p-0.5"
                                  disabled={deleteLiveMutation.isPending}
                                >
                                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/>
                                  </svg>
                                </button>
                              </div>
                            </div>
                          ))}
                        </div>
                      </SidebarGroupContent>
                    </SidebarGroup>
                  )}
                </>
              )
            })()}
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
              <Button size="sm" className="w-full" disabled={startLiveMutation.isPending || !selectedStrategy} onClick={handleSubmit(onValidStart)}>
                {startLiveMutation.isPending ? 'Starting...' : 'Start Live Trading'}
              </Button>
            )}
          </SidebarFooter>
        </Sidebar>

        {/* ── Right panel — live engine status ── */}
        <SidebarInset className="flex flex-col min-w-0">
          <LiveStatusPanel activeEngine={activeEngine} />
        </SidebarInset>

    </ResizableSidebarShell>
  )
}

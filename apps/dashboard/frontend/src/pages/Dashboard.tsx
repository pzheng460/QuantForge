import { useEffect, useState, useCallback } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { useDashboardStore } from '../stores/dashboardStore'
import { useShallow } from 'zustand/react/shallow'
import { useCatalog } from '../hooks/useCatalog'
import { useLiveEngines, useStartLive, useStopLive, useDeleteLive, useLiveAccount } from '../hooks/use-queries'
import type {
  LiveEngineOut,
  LiveStartRequest,
  LiveAccountOut,
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

// ─── Live report: engine registry + realtime broker position/trades ────────
// /live/engines now carries a realtime broker position snapshot and the
// engine's confirmed order submissions (each tied to a broker order id), so
// this panel shows truth from the adapter — never fabricated P&L. Every
// active engine gets its own block, because positions AND trade history are
// per-engine (two engines can share one venue account but not one trade log).

const statusKey: Record<string, string> = {
  running: 'dashboard.statusRunning',
  warmup: 'dashboard.statusWarmup',
  restarting: 'dashboard.statusRestarting',
  stopped: 'dashboard.statusStopped',
  failed: 'dashboard.statusFailed',
}

// Times are rendered in CST (UTC+8) to match the operator's timezone.
const cst = (iso?: string) =>
  iso ? new Date(iso).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '—'

function EnginePanel({ engine }: { engine: LiveEngineOut }) {
  const { trades = [] } = engine
  const { t } = useLang()

  return (
    <div className="rounded border border-border overflow-hidden">
      {/* Info bar — engine registry data */}
      <div className="px-3 py-2 border-b border-border flex items-center gap-3 flex-wrap">
        <StatusBadge status={engine.status} />
        <span className="text-xs text-foreground font-medium">{engine.strategy}</span>
        <span className="text-[10px] text-muted-foreground">{engine.symbol}</span>
        <span className="text-[10px] text-muted-foreground">{engine.timeframe}</span>
        <span className="text-[10px] text-muted-foreground">{engine.exchange}</span>
        {engine.demo && <Badge variant="warning" className="text-[9px]">DEMO</Badge>}
        {engine.leverage > 1 && <Badge variant="outline" className="text-[9px]">{engine.leverage}x</Badge>}
        <span className="ml-auto text-[10px] text-muted-foreground/70">
          {t('dashboard.engine')} {engine.engine_id}
          {engine.stopped_at
            ? <> · {t('dashboard.stopped')} {cst(engine.stopped_at)}</>
            : <> · {t('dashboard.started')} {cst(engine.created_at)}</>}
        </span>
      </div>

      <div className="p-4 space-y-4">
        {/* Engine status */}
        <div className="space-y-2">
          <div className="text-xs text-foreground font-semibold">{t('dashboard.engineStatus')}</div>
          <div className="text-[11px] text-muted-foreground leading-relaxed">
            {t(statusKey[engine.status] ?? engine.status)}
          </div>
          <div className="text-[10px] text-muted-foreground/80">
            {t('dashboard.enginePosition')}{' '}
            <span className={
              engine.owned_position?.side === 'long'
                ? 'text-tv-green font-semibold'
                : engine.owned_position?.side === 'short'
                  ? 'text-tv-red font-semibold'
                  : 'font-semibold text-foreground'
            }>
              {engine.owned_position?.side
                ? `${String(engine.owned_position.side).toUpperCase()} ${engine.owned_position.quantity}`
                : 'FLAT'}
            </span>
            {' '}<span className="text-muted-foreground/60">{t('dashboard.vsAccountPos')}</span>
          </div>
          {engine.error && (
            <div className="rounded bg-destructive/10 border border-destructive/40 px-2 py-1.5 text-[10.5px] text-destructive font-mono break-words">
              {engine.error}
            </div>
          )}
        </div>

        {/* Confirmed order submissions */}
        <div className="space-y-2">
          <div className="text-xs text-foreground font-semibold">{t('dashboard.recentTrades')}</div>
          {trades.length === 0 ? (
            <div className="text-[11px] text-muted-foreground">{t('dashboard.noOrders')}</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px] border-collapse">
                <thead>
                  <tr className="text-left text-muted-foreground/70">
                    <th className="py-1 pr-3 font-medium whitespace-nowrap">{t('dashboard.timeCst')}</th>
                    <th className="py-1 pr-3 font-medium">{t('dashboard.side')}</th>
                    <th className="py-1 pr-3 font-medium text-right">{t('dashboard.qty')}</th>
                    <th className="py-1 pr-3 font-medium text-right">{t('dashboard.price')}</th>
                    <th className="py-1 font-medium text-right">{t('dashboard.orderId')}</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.map((t) => (
                    <tr key={t.order_id} className="border-t border-border/60">
                      <td className="py-1.5 pr-3 text-muted-foreground font-mono whitespace-nowrap">{cst(t.time)}</td>
                      <td className="py-1.5 pr-3">
                        <span className={t.side === 'buy' ? 'text-tv-green font-medium' : 'text-tv-red font-medium'}>
                          {t.close ? 'CLOSE ' : ''}{String(t.side).toUpperCase()}
                        </span>
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-right">{t.quantity}</td>
                      <td className="py-1.5 pr-3 font-mono text-right tabular-nums">
                        ${Number(t.price).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </td>
                      <td className="py-1.5 text-muted-foreground font-mono text-right break-all">{t.order_id}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function LiveStatusPanel({ engines, account }: { engines: LiveEngineOut[]; account?: LiveAccountOut }) {
  const { t } = useLang()
  const active = engines.filter((e) => e.status === 'warmup' || e.status === 'running' || e.status === 'restarting')
  if (active.length === 0) {
    return (
      <div className="flex items-center justify-center h-full px-6">
        <div className="text-center max-w-md">
          <div className="text-muted-foreground text-lg mb-2">{t('dashboard.noLiveEngine')}</div>
          <div className="text-muted-foreground/60 text-xs leading-relaxed">
            {t('dashboard.emptyHint')}
          </div>
        </div>
      </div>
    )
  }

  const acctUpnl = account?.unrealized_pnl
  const acctUpnlClass = acctUpnl == null ? 'text-muted-foreground'
    : acctUpnl > 0 ? 'text-tv-green' : acctUpnl < 0 ? 'text-tv-red' : 'text-muted-foreground'

  const positions = account?.positions ?? []
  const posSideClass = (s?: string) => s === 'long' ? 'text-tv-green' : s === 'short' ? 'text-tv-red' : 'text-foreground'
  const posPnlClass = (v?: number | null) => v == null ? 'text-muted-foreground'
    : v > 0 ? 'text-tv-green' : v < 0 ? 'text-tv-red' : 'text-muted-foreground'

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0 overflow-y-auto bg-background">
        <div className="p-6 max-w-2xl mx-auto space-y-5">
          {/* Account-level summary — real numbers from the venue */}
          <div className="rounded border border-border p-4">
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs text-foreground font-semibold">{t('dashboard.accountSummary')}</div>
              <span className="text-[10px] text-muted-foreground/70">
                {account?.active_engines ?? 0} {t('dashboard.engines')} · {account?.trade_count ?? 0} {t('dashboard.trades')}
              </span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[11px]">
              <div className="space-y-0.5">
                <div className="text-muted-foreground/70">{t('dashboard.equity')}</div>
                <div className="font-mono font-semibold text-foreground tabular-nums">
                  {account?.equity != null ? `$${account.equity.toFixed(2)}` : '—'}
                </div>
              </div>
              <div className="space-y-0.5">
                <div className="text-muted-foreground/70">{t('dashboard.available')}</div>
                <div className="font-mono font-medium text-foreground tabular-nums">
                  {account?.available != null ? `$${account.available.toFixed(2)}` : '—'}
                </div>
              </div>
              <div className="space-y-0.5">
                <div className="text-muted-foreground/70">{t('dashboard.unrealizedPnl')}</div>
                <div className={`font-mono font-semibold tabular-nums ${acctUpnlClass}`}>
                  {acctUpnl != null ? `${acctUpnl >= 0 ? '+' : ''}$${acctUpnl.toFixed(2)}` : '—'}
                </div>
              </div>
              <div className="space-y-0.5">
                <div className="text-muted-foreground/70">{t('dashboard.positionValue')}</div>
                <div className="font-mono font-medium text-foreground tabular-nums">
                  {account?.position_value != null ? `$${account.position_value.toFixed(2)}` : '—'}
                </div>
              </div>
            </div>

            {/* Open positions are ACCOUNT-scoped (shared by all engines on
                this venue account) — rendered once here, not per engine. */}
            {positions.length > 0 && (
              <div className="mt-3 pt-3 border-t border-border space-y-2">
                <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                  {t('dashboard.openPositions')} <span className="normal-case">{t('dashboard.accountLevel')}</span>
                </div>
                {positions.map((p, i) => {
                  const mark = p.mark_price
                  const rate = p.profit_rate
                  return (
                    <div key={i} className="flex flex-wrap gap-x-5 gap-y-1.5 text-[11px]">
                      <span className="text-muted-foreground">{t('dashboard.symbol')}
                        <span className="ml-1.5 font-medium text-foreground">{p.symbol ?? '—'}</span>
                      </span>
                      <span className="text-muted-foreground">{t('dashboard.side')}
                        <span className={`ml-1.5 font-semibold ${posSideClass(p.side)}`}>{String(p.side ?? '—').toUpperCase()}</span>
                      </span>
                      <span className="text-muted-foreground">{t('dashboard.qty')}
                        <span className="ml-1.5 font-mono font-medium text-foreground">{p.quantity ?? '—'}</span>
                      </span>
                      <span className="text-muted-foreground">{t('dashboard.entry')}
                        <span className="ml-1.5 font-mono font-medium text-foreground">
                          {p.entry_price != null ? `$${Number(p.entry_price).toFixed(2)}` : '—'}
                        </span>
                      </span>
                      <span className="text-muted-foreground">{t('dashboard.mark')}
                        <span className="ml-1.5 font-mono font-medium text-foreground">
                          {mark != null ? `$${mark.toFixed(2)}` : '—'}
                        </span>
                      </span>
                      <span className="text-muted-foreground">{t('dashboard.upnl')}
                        <span className={`ml-1.5 font-mono font-semibold ${posPnlClass(p.unrealized_pnl)}`}>
                          {p.unrealized_pnl != null ? `${p.unrealized_pnl >= 0 ? '+' : ''}$${p.unrealized_pnl.toFixed(2)}` : '—'}
                        </span>
                      </span>
                      {rate != null && (
                        <span className="text-muted-foreground">{t('dashboard.return')}
                          <span className={`ml-1.5 font-mono font-semibold ${posPnlClass(rate)}`}>
                            {rate >= 0 ? '+' : ''}{(rate * 100).toFixed(2)}%
                          </span>
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {active.map((eng) => <EnginePanel key={eng.engine_id} engine={eng} />)}
        </div>
      </div>
    </div>
  )
}

// ─── Main Live Trading page ─────────────────────────────────────────────────

export default function DashboardPage() {
  const { t } = useLang()
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
  const { data: account } = useLiveAccount()
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
    <ResizableSidebarShell>
        {/* ── Left panel (Sidebar) ────────────────────────────────── */}
        <Sidebar collapsible="none" className="border-r border-border">
          <SidebarHeader className="px-3 py-2 border-b border-border flex-row items-center justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{t('dashboard.liveTrading')}</span>
            <div className="flex items-center gap-2">
              {globalRiskHalted && <Badge variant="destructive">HALTED</Badge>}
              {activeEngine && <StatusBadge status={activeEngine.status} />}
            </div>
          </SidebarHeader>

          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>{t('dashboard.globalRisk')}</SidebarGroupLabel>
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
                      next ? t('dashboard.emergencyReason') : t('dashboard.resumeReason'),
                    )
                      .then((state) => {
                        setGlobalRiskHalted(state.halted)
                        setGlobalRiskError(null)
                      })
                      .catch((error) => setGlobalRiskError(String(error)))
                  }}
                >
                  {globalRiskHalted ? t('dashboard.resumeGlobal') : t('dashboard.emergencyStop')}
                </Button>
                {globalRiskError && (
                  <div className="mt-1 text-[10px] text-destructive">{globalRiskError}</div>
                )}
              </SidebarGroupContent>
            </SidebarGroup>
            <SidebarGroup>
              <SidebarGroupLabel>{t('dashboard.strategy')}</SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-1">
                  <div className="flex flex-col gap-0.5 py-1">
                    <Label>{t('dashboard.strategy')}</Label>
                    <Select value={selectedStrategy} onValueChange={handleStrategyChange} disabled={!!activeEngine}>
                      <SelectTrigger className="text-xs h-7">
                        <SelectValue placeholder={t('dashboard.selectPlaceholder')} />
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
                <SidebarGroupLabel>{`${t('dashboard.parameters')} (${strategyParams.length})`}</SidebarGroupLabel>
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

            <SidebarGroup>
              <SidebarGroupLabel>{t('dashboard.settings')}</SidebarGroupLabel>
              <SidebarGroupContent>
                <div className="space-y-1">
                  <FormField label={t('dashboard.exchange')} error={formErrors.exchange?.message}>
                    <Controller
                      name="exchange"
                      control={control}
                      render={({ field }) => (
                        <Select value={field.value} onValueChange={(v) => { field.onChange(v); setExchange(v) }} disabled={!!activeEngine}>
                          <SelectTrigger className="text-xs h-7">
                            <SelectValue placeholder={t('dashboard.selectExchange')} />
                          </SelectTrigger>
                          <SelectContent>
                            {exchanges.map((ex) => <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </FormField>
                  <FormField label={t('dashboard.symbol')} error={formErrors.symbol?.message}>
                    <Input
                      className="text-xs h-7"
                      disabled={!!activeEngine}
                      {...register('symbol', {
                        onChange: (e) => setSymbol(e.target.value),
                      })}
                    />
                  </FormField>
                  {exchange === 'schwab' && <SchwabConnection />}
                  <FormField label={t('dashboard.timeframe')} error={formErrors.timeframe?.message}>
                    <Controller
                      name="timeframe"
                      control={control}
                      render={({ field }) => (
                        <Select value={field.value} onValueChange={(v) => { field.onChange(v); setTimeframe(v) }} disabled={!!activeEngine}>
                          <SelectTrigger className="text-xs h-7">
                            <SelectValue placeholder={t('dashboard.selectTimeframe')} />
                          </SelectTrigger>
                          <SelectContent>
                            {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => <SelectItem key={tf} value={tf}>{tf}</SelectItem>)}
                          </SelectContent>
                        </Select>
                      )}
                    />
                  </FormField>
                  <div className="flex gap-2">
                    <FormField label={t('dashboard.positionSize')} error={formErrors.positionSize?.message} className="flex-1">
                      <Input type="number" className="text-xs h-7" disabled={!!activeEngine}
                        {...register('positionSize', { valueAsNumber: true, onChange: (e) => setPositionSize(Number(e.target.value)) })}
                      />
                    </FormField>
                    <FormField label={t('dashboard.leverage')} error={formErrors.leverage?.message} className="flex-1">
                      <Input type="number" className="text-xs h-7" min={1} max={125} disabled={!!activeEngine}
                        {...register('leverage', { valueAsNumber: true, onChange: (e) => setLeverage(Number(e.target.value)) })}
                      />
                    </FormField>
                  </div>
                  <FormField label={t('dashboard.warmupBars')} error={formErrors.warmupBars?.message}>
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
                          <Label htmlFor="demo-toggle" className="text-[10px] cursor-pointer">{t('dashboard.demoMode')}</Label>
                        </div>
                        {!field.value && (
                          <div className="mt-1 rounded border border-destructive/40 bg-destructive/10 px-2 py-1.5 text-[10.5px] text-destructive leading-snug">
                            <span className="font-semibold">{t('dashboard.liveMode')}</span> — {t('dashboard.liveModeWarning')}
                            {t('dashboard.autoRiskNote')}
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
                      <SidebarGroupLabel>{`${t('dashboard.active')} (${active.length})`}</SidebarGroupLabel>
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
                      <SidebarGroupLabel>{`${t('dashboard.history')} (${archived.length})`}</SidebarGroupLabel>
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
                                    if (window.confirm(`${t('dashboard.deleteConfirmPrefix')} ${eng.strategy} (${eng.engine_id.slice(0, 8)}) ${t('dashboard.deleteConfirmSuffix')}`)) {
                                      deleteLiveMutation.mutate(eng.engine_id)
                                    }
                                  }}
                                  title={t('dashboard.deleteFromHistory')}
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
                {t('dashboard.stop')} {activeEngine.strategy}
              </Button>
            ) : (
              <Button size="sm" className="w-full" disabled={startLiveMutation.isPending || !selectedStrategy} onClick={handleSubmit(onValidStart)}>
                {startLiveMutation.isPending ? t('dashboard.starting') : t('dashboard.startLiveTrading')}
              </Button>
            )}
          </SidebarFooter>
        </Sidebar>

        {/* ── Right panel — live engine status ── */}
        <SidebarInset className="flex flex-col min-w-0">
          <LiveStatusPanel engines={engines} account={account} />
        </SidebarInset>

    </ResizableSidebarShell>
  )
}

import { useEffect, useRef, useCallback } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useLang } from '../i18n'
import { subscribeOptimize } from '../api/client'
import { useOptimizerStore } from '../stores/optimizerStore'
import { useCatalog } from '../hooks/useCatalog'
import { optimizeSchema, type OptimizeFormData } from '@/lib/schemas'
import { FormField } from '@/components/ui/form-field'
import { useRunOptimize, useCancelOptimize } from '../hooks/use-queries'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
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
import type {
  OptimizeRequest,
  OptimizeJobStatus,
  OptimizeProgress,
  GridSearchResult,
} from '../types'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const PERIODS: { value: string; label: string }[] = [
  { value: '1w', label: '1 week' },
  { value: '1m', label: '1 month' },
  { value: '3m', label: '3 months' },
  { value: '6m', label: '6 months' },
  { value: '1y', label: '1 year' },
  { value: '2y', label: '2 years' },
  { value: '3y', label: '3 years' },
  { value: '5y', label: '5 years' },
]
function pct(v: number, sign = true) {
  const s = sign && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}
function num(v: number, d = 2) { return v.toFixed(d) }

function StatusBadge({ status }: { status: string }) {
  const variantMap: Record<string, 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning'> = {
    pending: 'warning',
    running: 'default',
    completed: 'success',
    failed: 'destructive',
    cancelled: 'secondary',
  }
  return (
    <Badge
      variant={variantMap[status] ?? 'outline'}
      className={cn(status === 'running' && 'animate-pulse')}
    >
      {status}
    </Badge>
  )
}

// ─── Grid Search Results ──────────────────────────────────────────────────────

function formatDuration(secs: number): string {
  if (!isFinite(secs) || secs < 0) return '—'
  if (secs < 60) return `${Math.round(secs)}s`
  const m = Math.floor(secs / 60)
  const s = Math.round(secs - m * 60)
  if (m < 60) return s > 0 ? `${m}m ${s}s` : `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m - h * 60}m`
}

function GridProgressPanel({
  progress,
  status,
}: {
  progress?: OptimizeProgress
  status?: string
}) {
  const { t } = useLang()
  const total = progress?.total ?? 0
  const completed = progress?.completed ?? 0
  const pctVal = total > 0 ? Math.min(100, (completed / total) * 100) : 0
  const avgSecs = progress?.avg_secs_per_combo ?? null
  const elapsedSecs = progress?.elapsed_secs ?? null
  const remainingCombos = Math.max(0, total - completed)
  const etaSecs = avgSecs && remainingCombos > 0 ? avgSecs * remainingCombos : null

  const phase = !progress
    ? (status === 'pending' ? t('optimizer.queued') : t('optimizer.fetchingMarket'))
    : completed === 0
      ? t('optimizer.preparingGrid').replace('{total}', String(total))
      : completed >= total
        ? t('optimizer.finalizing')
        : t('optimizer.evaluating').replace('{completed}', String(completed)).replace('{total}', String(total))

  return (
    <div className="flex flex-1 items-center justify-center min-h-0">
      <div className="w-full max-w-md space-y-4">
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
              {t('optimizer.gridSearch')}
            </span>
            <span className="text-[12px] font-mono tabular-nums text-foreground">
              {total > 0 ? `${pctVal.toFixed(1)}%` : '—'}
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-[width] duration-300 ease-out',
                progress ? 'bg-brand' : 'bg-muted-foreground/40 animate-pulse',
              )}
              style={{ width: total > 0 ? `${pctVal}%` : '40%' }}
            />
          </div>
          <div className="flex items-baseline justify-between text-[11px] text-muted-foreground">
            <span>{phase}</span>
            {total > 0 && (
              <span className="font-mono tabular-nums">
                {completed} / {total}
              </span>
            )}
          </div>
          {(etaSecs !== null || elapsedSecs !== null) && (
            <div className="flex items-baseline justify-between text-[10px] text-muted-foreground/80 font-mono tabular-nums pt-0.5">
              {elapsedSecs !== null ? <span>{t('optimizer.elapsed').replace('{dur}', formatDuration(elapsedSecs))}</span> : <span />}
              {etaSecs !== null && (
                <span>
                  {t('optimizer.etaRemaining').replace('{dur}', formatDuration(etaSecs))}
                  {avgSecs ? t('optimizer.perCombo').replace('{secs}', avgSecs.toFixed(2)) : ''}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function GridResults({ r }: { r: GridSearchResult }) {
  const { t } = useLang()
  return (
    <div className="space-y-5">
      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-sm bg-muted">
        <div>
          <div className="text-xs text-muted-foreground">{t('optimizer.bestSharpe')}</div>
          <div className="text-xl font-semibold text-primary">{num(r.best_sharpe)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">{t('optimizer.bestReturn')}</div>
          <div className={cn('text-xl font-semibold', r.best_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
            {pct(r.best_return_pct)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">{t('optimizer.maxDrawdown')}</div>
          <div className="text-xl font-semibold text-tv-red">{pct(r.best_drawdown_pct, false)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">{t('optimizer.trainPeriod')}</div>
          <div className="text-sm font-medium text-foreground">{r.train_start} &rarr; {r.train_end}</div>
        </div>
      </div>

      {/* Best params */}
      <div>
        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">{t('optimizer.bestParameters')}</div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(r.best_params).map(([k, v]) => (
            <Badge key={k} variant="secondary" className="text-xs">
              <span className="text-muted-foreground">{k}:</span>{' '}
              <span className="font-medium text-foreground">{String(v)}</span>
            </Badge>
          ))}
        </div>
      </div>

      {/* Top N table */}
      <div>
        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
          {t('optimizer.topCombinations').replace('{n}', String(r.rows.length))}
        </div>
        <div className="overflow-x-auto">
          <table className="text-xs w-full">
            <thead>
              <tr className="border-b border-border">
                {['thRank', 'thSharpe', 'thReturn', 'thDrawdown', 'thTrades', 'thWin', 'thParameters'].map((k) => (
                  <th key={k} className="py-2 px-2 text-left text-muted-foreground font-medium">{t('optimizer.' + k)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row) => (
                <tr
                  key={row.rank}
                  className={cn(
                    'border-b border-border hover:bg-muted/50 transition-colors',
                    row.rank === 1 && 'bg-primary/5',
                  )}
                >
                  <td className="py-1.5 px-2 text-muted-foreground">{row.rank}</td>
                  <td className={cn('py-1.5 px-2 tabular-nums font-medium', row.sharpe >= 1 ? 'text-tv-green' : 'text-foreground')}>
                    {num(row.sharpe)}
                  </td>
                  <td className={cn('py-1.5 px-2 tabular-nums', row.total_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
                    {pct(row.total_return_pct)}
                  </td>
                  <td className="py-1.5 px-2 tabular-nums text-tv-red">{pct(row.max_drawdown_pct, false)}</td>
                  <td className="py-1.5 px-2 tabular-nums text-foreground">{row.total_trades}</td>
                  <td className="py-1.5 px-2 tabular-nums text-foreground">{num(row.win_rate_pct, 1)}%</td>
                  <td className="py-1.5 px-2">
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(row.params).map(([k, v]) => (
                        <span key={k} className="bg-muted rounded px-1.5 py-0.5 text-muted-foreground">
                          {k}=<span className="text-foreground">{String(v)}</span>
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function OptimizerPage() {
  const { strategies, exchanges } = useCatalog()
  const { t } = useLang()

  // Zustand store (persists across tab switches)
  const {
    strategy, setStrategy,
    exchange, setExchange,
    symbol, setSymbol,
    period, setPeriod,
    startDate, setStartDate,
    endDate, setEndDate,
    leverage, setLeverage,
    jobId, setJobId,
    status, setStatus,
    jobResult, setJobResult,
    error, setError,
    loading: _loading, setLoading,
    initialized, setInitialized,
  } = useOptimizerStore()

  // React Query mutations
  const runOptimizeMutation = useRunOptimize()
  const cancelOptimizeMutation = useCancelOptimize()

  // React Hook Form with Zod validation for configuration fields
  const {
    register: registerOpt,
    control: controlOpt,
    handleSubmit: handleSubmitOpt,
    formState: { errors: formErrors },
    setValue: _setFormValue,
  } = useForm<OptimizeFormData>({
    resolver: zodResolver(optimizeSchema),
    defaultValues: {
      strategy,
      exchange,
      symbol,
      leverage,
    },
  })

  const wsCleanupRef = useRef<(() => void) | null>(null)

  // Set default state on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      setInitialized(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  // WebSocket subscription for grid search -- reconnects on remount if job is still running
  useEffect(() => {
    if (!jobId) return
    if (status === 'completed' || status === 'failed') return
    wsCleanupRef.current?.()
    wsCleanupRef.current = subscribeOptimize(
      jobId,
      (msg: OptimizeJobStatus) => {
        setStatus(msg.status)
        // Always keep jobResult in sync so the progress bar (and other live
        // status fields) update during the run, not only at completion.
        setJobResult(msg)
        if (msg.status === 'completed') {
          setLoading(false)
        } else if (msg.status === 'failed') {
          setError(msg.error ?? t('optimizer.unknownError'))
          setLoading(false)
        }
      },
      (err: Event) => { setError(String(err)); setLoading(false) },
    )
    return () => { wsCleanupRef.current?.() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  // ─── Handlers ───────────────────────────────────────────────────────────────

  const onValidGridRun = useCallback((data: OptimizeFormData) => {
    // Sync validated data to Zustand
    setStrategy(data.strategy)
    setExchange(data.exchange)
    setSymbol(data.symbol ?? '')
    setLeverage(data.leverage)

    setLoading(true)
    setJobResult(null)
    setError(null)
    setStatus('pending')

    const req: OptimizeRequest = {
      strategy: data.strategy, exchange: data.exchange,
      symbol: data.symbol || undefined,
      leverage: data.leverage, mode: 'grid',
    }
    // Custom dates override Quick Period when both filled in.
    if (startDate && endDate) {
      req.start_date = startDate
      req.end_date = endDate
    } else {
      req.period = period
    }

    runOptimizeMutation.mutate(req, {
      onSuccess: (job) => setJobId(job.job_id),
      onError: (e) => { setError(String(e)); setLoading(false) },
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period, startDate, endDate])

  const handleCancel = useCallback(() => {
    if (jobId) {
      cancelOptimizeMutation.mutate(jobId, {
        onSuccess: () => { setStatus('cancelled'); setLoading(false) },
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  const selectedExchange = exchanges.find((e) => e.id === exchange)
  const isRunning = status === 'pending' || status === 'running'

  return (
    <ResizableSidebarShell>
      <Sidebar collapsible="none">
        <SidebarHeader className="border-b border-border px-3 py-2">
          <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            {t('optimizer.optimizerTitle')}
          </span>
        </SidebarHeader>

        <SidebarContent>
          {/* Configuration */}
          <SidebarGroup>
            <SidebarGroupLabel>{t('optimizer.configuration')}</SidebarGroupLabel>
            <SidebarGroupContent className="space-y-2">
              <FormField label={t('optimizer.strategy')} error={formErrors.strategy?.message}>
                <Controller
                  name="strategy"
                  control={controlOpt}
                  render={({ field }) => (
                <Select value={field.value || '__none__'} onValueChange={(v) => { const val = v === '__none__' ? '' : v; field.onChange(val); setStrategy(val) }}>
                  <SelectTrigger className="text-xs h-8">
                    <SelectValue placeholder={t('optimizer.selectStrategy')} />
                  </SelectTrigger>
                  <SelectContent>
                    {strategies.map((s) => <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>)}
                  </SelectContent>
                </Select>
                  )}
                />
              </FormField>

              <FormField label={t('optimizer.exchange')} error={formErrors.exchange?.message}>
                <Controller
                  name="exchange"
                  control={controlOpt}
                  render={({ field }) => (
                <Select value={field.value} onValueChange={(v) => { field.onChange(v); setExchange(v) }}>
                  <SelectTrigger className="text-xs h-8">
                    <SelectValue placeholder={t('optimizer.selectExchange')} />
                  </SelectTrigger>
                  <SelectContent>
                    {exchanges.filter((ex) => ex.supports_backtest !== false).map((ex) => <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                  )}
                />
              </FormField>

              <FormField label={t('optimizer.symbolDefault').replace('{sym}', selectedExchange?.default_symbol ?? '...')} error={formErrors.symbol?.message}>
                <Input
                  type="text"
                  className="text-xs h-8"
                  placeholder={selectedExchange?.default_symbol ?? ''}
                  {...registerOpt('symbol', {
                    onChange: (e) => setSymbol(e.target.value),
                  })}
                />
              </FormField>

              <FormField label={t('optimizer.leverage')} error={formErrors.leverage?.message}>
                <Input
                  type="number"
                  className="text-xs h-8"
                  min={1}
                  max={20}
                  step={1}
                  {...registerOpt('leverage', {
                    valueAsNumber: true,
                    onChange: (e) => setLeverage(Number(e.target.value)),
                  })}
                />
              </FormField>
            </SidebarGroupContent>
          </SidebarGroup>

          {/* Grid-specific: period / date range / parallel jobs */}
          <SidebarGroup>
              <SidebarGroupLabel>{t('optimizer.period')}</SidebarGroupLabel>
              <SidebarGroupContent className="space-y-2">
                <p className="text-[10px] text-muted-foreground leading-snug">
                  {t('optimizer.lookbackPrefix')} <span className="font-medium text-foreground">{t('optimizer.lookbackBold')}</span>{' '}
                  {t('optimizer.lookbackSuffix')}
                </p>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">{t('optimizer.lookbackLabel')}</Label>
                  <Select value={period} onValueChange={setPeriod}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder={t('optimizer.selectLookback')} />
                    </SelectTrigger>
                    <SelectContent>
                      {PERIODS.map((p) => (
                        <SelectItem key={p.value} value={p.value}>{t('optimizer.' + p.value)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-2 py-0.5">
                  <div className="flex-1 h-px bg-border" />
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">{t('optimizer.orPickDates')}</span>
                  <div className="flex-1 h-px bg-border" />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">{t('optimizer.startDate')}</Label>
                  <Input
                    type="date"
                    className="text-xs h-8"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">{t('optimizer.endDate')}</Label>
                  <Input
                    type="date"
                    className="text-xs h-8"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
                {startDate && endDate && (
                  <p className="text-[10px] text-muted-foreground">
                    {t('optimizer.usingPrefix')} <span className="font-mono text-foreground">{startDate} → {endDate}</span> {t('optimizer.usingSuffix')}
                  </p>
                )}
              </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter className="border-t border-border p-3 space-y-2">
          {isRunning ? (
            <Button
              variant="destructive"
              size="sm"
              className="w-full"
              onClick={handleCancel}
            >
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
              {t('optimizer.cancelGrid')}
            </Button>
          ) : (
            <Button
              size="sm"
              className="w-full"
              onClick={handleSubmitOpt(onValidGridRun)}
              disabled={!strategy}
            >
              {t('optimizer.runGrid')}
            </Button>
          )}
          <div className="flex items-center gap-2">
            {status && <StatusBadge status={status} />}
            {isRunning && (
              <span className="text-[10px] text-muted-foreground">{t('optimizer.takeSeveralMinutes')}</span>
            )}
          </div>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset>
        <div className="h-full flex flex-col p-6 overflow-hidden">
          {/* ── Error displays ───────────────────────────────────────── */}
          {error && (
            <Card className="border-destructive/50 mb-4 shrink-0">
              <CardContent className="pt-4">
                <p className="text-sm font-medium text-red-500 mb-1">{t('optimizer.gridFailed')}</p>
                <pre className="text-xs text-muted-foreground whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
              </CardContent>
            </Card>
          )}

          {/* ── Grid Search progress ─────────────────────────────────── */}
          {isRunning && (
            <GridProgressPanel progress={jobResult?.progress} status={status} />
          )}

          {/* ── Grid Results ─────────────────────────────────────────── */}
          {jobResult?.status === 'completed' && jobResult.grid_result && (
            <div className="flex-1 min-h-0 overflow-y-auto">
              <GridResults r={jobResult.grid_result} />
            </div>
          )}

          {/* Empty state */}
          {!isRunning && !error && jobResult?.status !== 'completed' && (
            <div className="flex flex-1 items-center justify-center min-h-0">
              <div className="text-center max-w-md">
                <div className="text-muted-foreground text-lg mb-2">{t('optimizer.noResults')}</div>
                <div className="text-muted-foreground/60 text-xs leading-relaxed">
                  {t('optimizer.emptyHint')}
                </div>
              </div>
            </div>
          )}
        </div>
      </SidebarInset>
    </ResizableSidebarShell>
  )
}

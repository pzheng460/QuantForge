import { useEffect, useRef, useCallback } from 'react'
import { useForm, Controller } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import { subscribeOptimize, subscribeAgent, ApiError } from '../api/client'
import { useOptimizerStore } from '../stores/optimizerStore'
import { useCatalog } from '../hooks/useCatalog'
import { optimizeSchema, type OptimizeFormData } from '@/lib/schemas'
import { FormField } from '@/components/ui/form-field'
import {
  useAgentSkills,
  useAgentStatus,
  useRunOptimize,
  useRunAgent,
  useCancelOptimize,
  useStopAgent,
} from '../hooks/use-queries'
import AgentTraceViewer from '../components/AgentTraceViewer'
import MetricsSummary from '../components/MetricsSummary'
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
  AgentRunRequest,
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
const MODES = [
  { value: 'grid', label: 'Grid Search', desc: 'Python-based parameter grid optimization' },
  { value: 'ai', label: 'AI Optimize', desc: 'Claude Code-driven iterative optimization' },
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
  const total = progress?.total ?? 0
  const completed = progress?.completed ?? 0
  const pctVal = total > 0 ? Math.min(100, (completed / total) * 100) : 0
  const avgSecs = progress?.avg_secs_per_combo ?? null
  const elapsedSecs = progress?.elapsed_secs ?? null
  const remainingCombos = Math.max(0, total - completed)
  const etaSecs = avgSecs && remainingCombos > 0 ? avgSecs * remainingCombos : null

  const phase = !progress
    ? (status === 'pending' ? 'Queued…' : 'Fetching market data…')
    : completed === 0
      ? `Preparing grid (${total} combinations)`
      : completed >= total
        ? 'Finalizing…'
        : `Evaluating combination ${completed} / ${total}`

  return (
    <div className="flex flex-1 items-center justify-center min-h-0">
      <div className="w-full max-w-md space-y-4">
        <div className="space-y-1.5">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-mono uppercase tracking-[0.12em] text-muted-foreground">
              Grid Search
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
              {elapsedSecs !== null ? <span>elapsed {formatDuration(elapsedSecs)}</span> : <span />}
              {etaSecs !== null && (
                <span>
                  ≈ {formatDuration(etaSecs)} remaining
                  {avgSecs ? ` · ${avgSecs.toFixed(2)}s / combo` : ''}
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
  return (
    <div className="space-y-5">
      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 rounded-sm bg-muted">
        <div>
          <div className="text-xs text-muted-foreground">Best Sharpe</div>
          <div className="text-xl font-semibold text-primary">{num(r.best_sharpe)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Best Return</div>
          <div className={cn('text-xl font-semibold', r.best_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
            {pct(r.best_return_pct)}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Max Drawdown</div>
          <div className="text-xl font-semibold text-tv-red">{pct(r.best_drawdown_pct, false)}</div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground">Train Period</div>
          <div className="text-sm font-medium text-foreground">{r.train_start} &rarr; {r.train_end}</div>
        </div>
      </div>

      {/* Best params */}
      <div>
        <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Best Parameters</div>
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
          Top {r.rows.length} Combinations
        </div>
        <div className="overflow-x-auto">
          <table className="text-xs w-full">
            <thead>
              <tr className="border-b border-border">
                {['Rank', 'Sharpe', 'Return', 'Drawdown', 'Trades', 'Win%', 'Parameters'].map((h) => (
                  <th key={h} className="py-2 px-2 text-left text-muted-foreground font-medium">{h}</th>
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

  // Zustand store (persists across tab switches)
  const {
    strategy, setStrategy,
    exchange, setExchange,
    symbol, setSymbol,
    period, setPeriod,
    startDate, setStartDate,
    endDate, setEndDate,
    leverage, setLeverage,
    mode, setMode,
    nJobs, setNJobs,
    jobId, setJobId,
    status, setStatus,
    jobResult, setJobResult,
    error, setError,
    loading: _loading, setLoading,
    initialized, setInitialized,
  } = useOptimizerStore()

  // Agent-specific state (persisted in store across tab switches)
  const {
    agentJobId, setAgentJobId,
    agentEvents, addAgentEvent,
    agentError, setAgentError,
    agentSkills: storedSkills, setAgentSkills,
    selectedSkill, setSelectedSkill,
    resetAgent,
  } = useOptimizerStore()

  // React Query: load agent skills
  const agentSkillsQuery = useAgentSkills()

  // Sync React Query agent skills into Zustand (so the rest of the component works)
  const agentSkills = agentSkillsQuery.data ?? storedSkills
  useEffect(() => {
    if (agentSkillsQuery.data) {
      setAgentSkills(agentSkillsQuery.data)
      if (agentSkillsQuery.data.length > 0 && !selectedSkill) {
        setSelectedSkill(agentSkillsQuery.data[0].name)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentSkillsQuery.data])

  // React Query mutations
  const runOptimizeMutation = useRunOptimize()
  const cancelOptimizeMutation = useCancelOptimize()
  const runAgentMutation = useRunAgent()
  const stopAgentMutation = useStopAgent()

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

  // Derive agentStatus from store
  const { agentStatus, setAgentStatus } = useOptimizerStore()

  // React Query: poll agent status (replaces manual setInterval)
  const isAgentPolling = !!agentJobId && agentStatus !== 'completed' && agentStatus !== 'failed' && agentStatus !== 'cancelled'
  const agentStatusQuery = useAgentStatus(agentJobId, isAgentPolling)

  // Sync agent status query into Zustand
  useEffect(() => {
    if (!agentStatusQuery.data) return
    const job = agentStatusQuery.data
    setAgentStatus(job.status)
    if (job.error) setAgentError(job.error)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentStatusQuery.data])

  // Detect 404 — backend lost the job (typically due to uvicorn --reload
  // restarting the process and clearing in-memory state). Auto-reset so
  // the user isn't stuck polling a phantom job.
  useEffect(() => {
    const err = agentStatusQuery.error
    if (err instanceof ApiError && err.status === 404) {
      console.warn('[optimizer] agent job lost on backend (404) — resetting')
      resetAgent()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentStatusQuery.error])

  // Set default state on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      setInitialized(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  // WebSocket subscription for grid search -- reconnects on remount if job is still running
  useEffect(() => {
    if (!jobId || mode !== 'grid') return
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
          setError(msg.error ?? 'Unknown error')
          setLoading(false)
        }
      },
      (err: Event) => { setError(String(err)); setLoading(false) },
    )
    return () => { wsCleanupRef.current?.() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, mode])

  // WebSocket subscription for AI agent
  useEffect(() => {
    if (!agentJobId) return
    if (agentStatus === 'completed' || agentStatus === 'failed' || agentStatus === 'cancelled') return

    const cleanup = subscribeAgent(
      agentJobId,
      (event) => { addAgentEvent(event) },
      () => { console.warn('Agent WS disconnected') },
    )
    return cleanup
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentJobId, agentStatus])

  // Agent status polling is now handled by React Query (useAgentStatus above)

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
      leverage: data.leverage, mode: 'grid', n_jobs: nJobs,
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
  }, [nJobs, period, startDate, endDate])

  const onValidAIRun = useCallback((data: OptimizeFormData) => {
    if (!selectedSkill) return
    setStrategy(data.strategy)
    setExchange(data.exchange)
    setSymbol(data.symbol ?? '')
    setLeverage(data.leverage)

    resetAgent()
    setAgentStatus('pending')

    const req: AgentRunRequest = {
      skill_path: selectedSkill,
      strategy: data.strategy,
      exchange: data.exchange,
      symbol: data.symbol || undefined,
      timeframe: '1h',
      max_iterations: 5,
    }
    runAgentMutation.mutate(req, {
      onSuccess: (job) => setAgentJobId(job.job_id),
      onError: (e) => setAgentError(String(e)),
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSkill])

  const handleCancel = useCallback(() => {
    if (mode === 'grid' && jobId) {
      cancelOptimizeMutation.mutate(jobId, {
        onSuccess: () => { setStatus('cancelled'); setLoading(false) },
      })
    } else if (mode === 'ai' && agentJobId) {
      stopAgentMutation.mutate(agentJobId, {
        onSuccess: () => setAgentStatus('cancelled'),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, jobId, agentJobId])

  const selectedExchange = exchanges.find((e) => e.id === exchange)
  const selectedSkillInfo = agentSkills.find((s) => s.name === selectedSkill)
  const isRunning =
    (mode === 'grid' && (status === 'pending' || status === 'running')) ||
    (mode === 'ai' && (agentStatus === 'pending' || agentStatus === 'running'))

  return (
    <ResizableSidebarShell storageKey="optimizer">
      <Sidebar collapsible="none">
        <SidebarHeader className="border-b border-border px-3 py-2">
          <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
            Optimizer
          </span>
        </SidebarHeader>

        <SidebarContent>
          {/* Mode selector */}
          <SidebarGroup>
            <SidebarGroupLabel>Mode</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="grid grid-cols-2 gap-2">
                {MODES.map((m) => (
                  <Button
                    key={m.value}
                    variant="outline"
                    size="sm"
                    onClick={() => setMode(m.value as 'grid' | 'ai')}
                    className={cn(
                      'h-auto text-left p-2 rounded-sm justify-start flex-col items-start whitespace-normal',
                      mode === m.value && 'border-primary bg-primary/10 text-primary',
                    )}
                  >
                    <span className="text-xs font-medium">{m.label}</span>
                    <span className="text-[10px] text-muted-foreground leading-tight">{m.desc}</span>
                  </Button>
                ))}
              </div>
            </SidebarGroupContent>
          </SidebarGroup>

          {/* Configuration */}
          <SidebarGroup>
            <SidebarGroupLabel>Configuration</SidebarGroupLabel>
            <SidebarGroupContent className="space-y-2">
              <FormField label="Strategy" error={formErrors.strategy?.message}>
                <Controller
                  name="strategy"
                  control={controlOpt}
                  render={({ field }) => (
                <Select value={field.value || '__none__'} onValueChange={(v) => { const val = v === '__none__' ? '' : v; field.onChange(val); setStrategy(val) }}>
                  <SelectTrigger className="text-xs h-8">
                    <SelectValue placeholder="Select strategy" />
                  </SelectTrigger>
                  <SelectContent>
                    {strategies.map((s) => <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>)}
                  </SelectContent>
                </Select>
                  )}
                />
              </FormField>

              <FormField label="Exchange" error={formErrors.exchange?.message}>
                <Controller
                  name="exchange"
                  control={controlOpt}
                  render={({ field }) => (
                <Select value={field.value} onValueChange={(v) => { field.onChange(v); setExchange(v) }}>
                  <SelectTrigger className="text-xs h-8">
                    <SelectValue placeholder="Select exchange" />
                  </SelectTrigger>
                  <SelectContent>
                    {exchanges.filter((ex) => ex.supports_backtest !== false).map((ex) => <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>)}
                  </SelectContent>
                </Select>
                  )}
                />
              </FormField>

              <FormField label={`Symbol (default: ${selectedExchange?.default_symbol ?? '...'})`} error={formErrors.symbol?.message}>
                <Input
                  type="text"
                  className="text-xs h-8"
                  placeholder={selectedExchange?.default_symbol ?? ''}
                  {...registerOpt('symbol', {
                    onChange: (e) => setSymbol(e.target.value),
                  })}
                />
              </FormField>

              <FormField label="Leverage" error={formErrors.leverage?.message}>
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
          {mode === 'grid' && (
            <SidebarGroup>
              <SidebarGroupLabel>Period</SidebarGroupLabel>
              <SidebarGroupContent className="space-y-2">
                <p className="text-[10px] text-muted-foreground leading-snug">
                  Lookback is used <span className="font-medium text-foreground">unless both dates below are filled</span>{' '}
                  (then dates override).
                </p>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">Lookback (from today)</Label>
                  <Select value={period} onValueChange={setPeriod}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="Select lookback" />
                    </SelectTrigger>
                    <SelectContent>
                      {PERIODS.map((p) => (
                        <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-2 py-0.5">
                  <div className="flex-1 h-px bg-border" />
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground">or pick exact dates (overrides)</span>
                  <div className="flex-1 h-px bg-border" />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">Start Date</Label>
                  <Input
                    type="date"
                    className="text-xs h-8"
                    value={startDate}
                    onChange={(e) => setStartDate(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-xs">End Date</Label>
                  <Input
                    type="date"
                    className="text-xs h-8"
                    value={endDate}
                    onChange={(e) => setEndDate(e.target.value)}
                  />
                </div>
                {startDate && endDate && (
                  <p className="text-[10px] text-muted-foreground">
                    Using <span className="font-mono text-foreground">{startDate} → {endDate}</span> (overrides period)
                  </p>
                )}

                <div className="flex flex-col gap-1">
                  <Label className="text-xs">Parallel Jobs</Label>
                  <Select value={String(nJobs)} onValueChange={(v) => setNJobs(Number(v))}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="Jobs" />
                    </SelectTrigger>
                    <SelectContent>
                      {[1, 2, 4, 8, -1].map((n) => (
                        <SelectItem key={n} value={String(n)}>{n === -1 ? 'All CPUs' : n}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </SidebarGroupContent>
            </SidebarGroup>
          )}

          {/* AI-specific: skill selector */}
          {mode === 'ai' && (
            <SidebarGroup>
              <SidebarGroupLabel>AI Skill</SidebarGroupLabel>
              <SidebarGroupContent className="space-y-2">
                <Select value={selectedSkill} onValueChange={setSelectedSkill}>
                  <SelectTrigger className="text-xs h-8">
                    <SelectValue placeholder="Select skill" />
                  </SelectTrigger>
                  <SelectContent>
                    {agentSkills.map((s) => (
                      <SelectItem key={s.name} value={s.name}>{s.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {selectedSkillInfo && (
                  <p className="text-xs text-muted-foreground">{selectedSkillInfo.description}</p>
                )}
              </SidebarGroupContent>
            </SidebarGroup>
          )}
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
              Cancel {mode === 'grid' ? 'Grid Search' : 'AI Optimize'}
            </Button>
          ) : (
            <Button
              size="sm"
              className="w-full"
              onClick={handleSubmitOpt(mode === 'grid' ? onValidGridRun : onValidAIRun)}
              disabled={!strategy || (mode === 'ai' && !selectedSkill)}
            >
              {mode === 'grid' ? 'Run Grid Search' : 'Run AI Optimize'}
            </Button>
          )}
          <div className="flex items-center gap-2">
            {mode === 'grid' && status && <StatusBadge status={status} />}
            {mode === 'ai' && agentStatus && <StatusBadge status={agentStatus} />}
            {isRunning && (
              <span className="text-[10px] text-muted-foreground">This may take several minutes&hellip;</span>
            )}
          </div>
        </SidebarFooter>
      </Sidebar>

      <SidebarInset>
        <div className="h-full flex flex-col p-6 overflow-hidden">
          {/* ── Error displays ───────────────────────────────────────── */}
          {error && mode === 'grid' && (
            <Card className="border-destructive/50 mb-4 shrink-0">
              <CardContent className="pt-4">
                <p className="text-sm font-medium text-red-500 mb-1">Grid search failed</p>
                <pre className="text-xs text-muted-foreground whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
              </CardContent>
            </Card>
          )}

          {agentError && mode === 'ai' && (
            <Card className="border-destructive/50 mb-4 shrink-0">
              <CardContent className="pt-4">
                <p className="text-sm font-medium text-red-500 mb-1">AI optimization failed</p>
                <pre className="text-xs text-red-400 whitespace-pre-wrap overflow-auto max-h-48">{agentError}</pre>
              </CardContent>
            </Card>
          )}

          {/* ── Grid Search progress ─────────────────────────────────── */}
          {mode === 'grid' && isRunning && (
            <GridProgressPanel progress={jobResult?.progress} status={status} />
          )}

          {/* ── Grid Results ─────────────────────────────────────────── */}
          {mode === 'grid' && jobResult?.status === 'completed' && jobResult.grid_result && (
            <div className="flex-1 min-h-0 overflow-y-auto">
              <GridResults r={jobResult.grid_result} />
            </div>
          )}

          {/* ── AI Trace Viewer ──────────────────────────────────────── */}
          {mode === 'ai' && agentJobId && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-1 min-h-0">
              <Card className="lg:col-span-2 overflow-hidden flex flex-col">
                <CardContent className="flex-1 min-h-0 p-0">
                  <AgentTraceViewer events={agentEvents} status={agentStatus} className="h-full" />
                </CardContent>
              </Card>
              <Card className="overflow-hidden flex flex-col">
                <CardContent className="p-0 flex-1 min-h-0">
                  <MetricsSummary
                    events={agentEvents}
                    metrics={selectedSkillInfo?.metrics ?? []}
                  />
                </CardContent>
              </Card>
            </div>
          )}

          {/* Empty state */}
          {!isRunning && !error && !agentError && !(mode === 'grid' && jobResult?.status === 'completed') && !(mode === 'ai' && agentJobId) && (
            <div className="flex flex-1 items-center justify-center min-h-0">
              <div className="text-center max-w-md">
                <div className="text-muted-foreground text-lg mb-2">No Results Yet</div>
                <div className="text-muted-foreground/60 text-xs leading-relaxed">
                  Configure your optimization settings in the sidebar, then click Run to start.
                </div>
              </div>
            </div>
          )}
        </div>
      </SidebarInset>
    </ResizableSidebarShell>
  )
}

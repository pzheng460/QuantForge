import { useEffect, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'
import { api, subscribeOptimize, subscribeAgent } from '../api/client'
import { useOptimizerStore } from '../stores/optimizerStore'
import { useCatalog } from '../hooks/useCatalog'
import AgentTraceViewer from '../components/AgentTraceViewer'
import MetricsSummary from '../components/MetricsSummary'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import type {
  OptimizeRequest,
  OptimizeJobStatus,
  GridSearchResult,
  AgentRunRequest,
  AgentEvent,
  AgentSkillInfo,
} from '../types'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const PERIODS = ['1m', '3m', '6m', '1y', '2y', '3y', '5y']
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
    useDateRange, setUseDateRange,
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
    loading, setLoading,
    initialized, setInitialized,
  } = useOptimizerStore()

  // Agent-specific state (persisted in store across tab switches)
  const {
    agentJobId, setAgentJobId,
    agentStatus, setAgentStatus,
    agentEvents, addAgentEvent,
    agentError, setAgentError,
    agentSkills, setAgentSkills: _setSkills,
    selectedSkill, setSelectedSkill,
    resetAgent,
  } = useOptimizerStore()

  const setAgentSkills = _setSkills

  const wsCleanupRef = useRef<(() => void) | null>(null)

  // Load agent skills
  useEffect(() => {
    api.agentSkills()
      .then((skills) => {
        setAgentSkills(skills)
        if (skills.length > 0 && !selectedSkill) {
          setSelectedSkill(skills[0].name)
        }
      })
      .catch(() => {})
  }, [selectedSkill])

  // Set default state on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      setInitialized(true)
    }
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
        if (msg.status === 'completed') {
          setJobResult(msg)
          setLoading(false)
        } else if (msg.status === 'failed') {
          setError(msg.error ?? 'Unknown error')
          setLoading(false)
        }
      },
      (err) => { setError(String(err)); setLoading(false) },
    )
    return () => wsCleanupRef.current?.()
  }, [jobId, mode])

  // WebSocket subscription for AI agent
  useEffect(() => {
    if (!agentJobId) return
    if (agentStatus === 'completed' || agentStatus === 'failed' || agentStatus === 'cancelled') return

    const cleanup = subscribeAgent(
      agentJobId,
      (event) => { addAgentEvent(event) },
      () => { console.warn('Agent WS disconnected, status poll will detect real errors') },
    )

    return cleanup
  }, [agentJobId, agentStatus])

  // Poll agent status
  useEffect(() => {
    if (!agentJobId) return

    const interval = setInterval(async () => {
      try {
        const agentJob = await api.getAgentStatus(agentJobId)
        setAgentStatus(agentJob.status)
        if (agentJob.error) setAgentError(agentJob.error)
        if (agentJob.status === 'completed' || agentJob.status === 'failed') {
          clearInterval(interval)
        }
      } catch (err) {
        console.error('Failed to poll agent status:', err)
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [agentJobId])

  // ─── Handlers ───────────────────────────────────────────────────────────────

  const handleGridRun = useCallback(async () => {
    setLoading(true)
    setJobResult(null)
    setError(null)
    setStatus('pending')

    const req: OptimizeRequest = {
      strategy, exchange,
      symbol: symbol || undefined,
      leverage, mode: 'grid', n_jobs: nJobs,
    }
    if (useDateRange) {
      req.start_date = startDate || undefined
      req.end_date = endDate || undefined
    } else {
      req.period = period
    }

    try {
      const job = await api.runOptimize(req)
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e)); setLoading(false)
    }
  }, [strategy, exchange, symbol, leverage, nJobs, useDateRange, period, startDate, endDate])

  const handleAIRun = useCallback(async () => {
    if (!selectedSkill) return

    resetAgent()
    setAgentStatus('pending')

    const req: AgentRunRequest = {
      skill_path: selectedSkill,
      strategy,
      exchange,
      symbol: symbol || undefined,
      timeframe: '1h',
      max_iterations: 5,
    }

    try {
      const job = await api.runAgent(req)
      setAgentJobId(job.job_id)
    } catch (e) {
      setAgentError(String(e))
    }
  }, [selectedSkill, strategy, exchange, symbol])

  const handleCancel = useCallback(async () => {
    if (mode === 'grid' && jobId) {
      try {
        await api.cancelOptimize(jobId)
        setStatus('cancelled')
        setLoading(false)
      } catch { /* ignore */ }
    } else if (mode === 'ai' && agentJobId) {
      try {
        await api.stopAgent(agentJobId)
        setAgentStatus('cancelled')
      } catch { /* ignore */ }
    }
  }, [mode, jobId, agentJobId])

  const selectedExchange = exchanges.find((e) => e.id === exchange)
  const selectedSkillInfo = agentSkills.find((s) => s.name === selectedSkill)
  const isRunning =
    (mode === 'grid' && (status === 'pending' || status === 'running')) ||
    (mode === 'ai' && (agentStatus === 'pending' || agentStatus === 'running'))

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-foreground">Optimizer</h1>

      {/* ── Configuration Card ────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle>Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">

          {/* Mode selector */}
          <div>
            <Label className="mb-2 block">Optimization Mode</Label>
            <div className="grid grid-cols-2 gap-2">
              {MODES.map((m) => (
                <Button
                  variant="outline"
                  key={m.value}
                  onClick={() => setMode(m.value as 'grid' | 'ai')}
                  className={cn(
                    'h-auto text-left p-3 rounded-sm justify-start flex-col items-start whitespace-normal',
                    mode === m.value
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border hover:border-muted-foreground text-foreground',
                  )}
                >
                  <div className="font-medium text-sm">{m.label}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">{m.desc}</div>
                </Button>
              ))}
            </div>
          </div>

          {/* AI skill selector (only for AI mode) */}
          {mode === 'ai' && (
            <div className="flex flex-col gap-1">
              <Label>AI Skill</Label>
              <Select value={selectedSkill} onChange={(e) => setSelectedSkill(e.target.value)}>
                {agentSkills.map((skill) => (
                  <option key={skill.name} value={skill.name}>{skill.name}</option>
                ))}
              </Select>
              {selectedSkillInfo && (
                <p className="text-xs text-muted-foreground mt-1">{selectedSkillInfo.description}</p>
              )}
            </div>
          )}

          {/* Row: strategy + exchange + symbol + leverage */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="flex flex-col gap-1">
              <Label>Strategy</Label>
              <Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                <option value="">-- Select --</option>
                {strategies.map((s) => <option key={s.name} value={s.name}>{s.display_name}</option>)}
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <Label>Exchange</Label>
              <Select value={exchange} onChange={(e) => setExchange(e.target.value)}>
                {exchanges.map((ex) => <option key={ex.id} value={ex.id}>{ex.name}</option>)}
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <Label>Symbol (default: {selectedExchange?.default_symbol ?? '...'})</Label>
              <Input
                type="text"
                placeholder={selectedExchange?.default_symbol ?? ''}
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label>Leverage</Label>
              <Input
                type="number"
                min={1}
                max={20}
                step={1}
                value={leverage}
                onChange={(e) => setLeverage(Number(e.target.value))}
              />
            </div>
          </div>

          {/* Period / Date range (only for grid search) */}
          {mode === 'grid' && (
            <div className="flex flex-wrap gap-4 items-end">
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox
                  checked={useDateRange}
                  onCheckedChange={(checked) => setUseDateRange(checked === true)}
                />
                <span className="text-sm text-foreground">Custom date range</span>
              </label>

              {!useDateRange ? (
                <div className="flex flex-col gap-1">
                  <Label>Period</Label>
                  <Select value={period} onChange={(e) => setPeriod(e.target.value)}>
                    {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
                  </Select>
                </div>
              ) : (
                <>
                  <div className="flex flex-col gap-1">
                    <Label>Start date</Label>
                    <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label>End date</Label>
                    <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                  </div>
                </>
              )}

              <div className="flex flex-col gap-1">
                <Label>Parallel jobs</Label>
                <Select className="w-24" value={nJobs} onChange={(e) => setNJobs(Number(e.target.value))}>
                  {[1, 2, 4, 8, -1].map((n) => (
                    <option key={n} value={n}>{n === -1 ? 'All CPUs' : n}</option>
                  ))}
                </Select>
              </div>
            </div>
          )}

          {/* Action bar */}
          <div className="flex items-center gap-3 pt-1">
            <Button
              onClick={mode === 'grid' ? handleGridRun : handleAIRun}
              disabled={
                isRunning || !strategy ||
                (mode === 'ai' && !selectedSkill)
              }
            >
              {mode === 'grid'
                ? (isRunning ? 'Running\u2026' : 'Run Grid Search')
                : (isRunning ? 'Running\u2026' : 'Run AI Optimize')}
            </Button>

            {isRunning && (
              <Button variant="destructive" size="sm" onClick={handleCancel}>
                Cancel
              </Button>
            )}

            {mode === 'grid' && status && <StatusBadge status={status} />}
            {mode === 'ai' && agentStatus && <StatusBadge status={agentStatus} />}

            {isRunning && (
              <span className="text-xs text-muted-foreground">This may take several minutes&hellip;</span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* ── Error cards ───────────────────────────────────────────────── */}
      {error && mode === 'grid' && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-red-500 mb-1">Grid search failed</p>
            <pre className="text-xs text-red-400 whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
          </CardContent>
        </Card>
      )}

      {agentError && mode === 'ai' && (
        <Card className="border-destructive/50">
          <CardContent className="pt-4">
            <p className="text-sm font-medium text-red-500 mb-1">AI optimization failed</p>
            <pre className="text-xs text-red-400 whitespace-pre-wrap overflow-auto max-h-48">{agentError}</pre>
          </CardContent>
        </Card>
      )}

      {/* ── Grid Results ──────────────────────────────────────────────── */}
      {mode === 'grid' && jobResult?.status === 'completed' && jobResult.grid_result && (
        <Card>
          <CardHeader>
            <CardTitle>
              Results{' '}
              <span className="font-normal text-muted-foreground">
                Grid Search &middot; {strategy} &middot; {exchange}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <GridResults r={jobResult.grid_result} />
          </CardContent>
        </Card>
      )}

      {/* ── AI Trace Viewer ───────────────────────────────────────────── */}
      {mode === 'ai' && agentJobId && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 h-[600px]">
          <Card className="lg:col-span-2 overflow-hidden flex flex-col">
            <CardContent className="flex-1 overflow-auto p-0">
              <AgentTraceViewer events={agentEvents} status={agentStatus} className="h-full" />
            </CardContent>
          </Card>
          <Card className="overflow-hidden">
            <CardContent className="p-0 h-full">
              <MetricsSummary
                events={agentEvents}
                metrics={selectedSkillInfo?.metrics ?? []}
              />
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}

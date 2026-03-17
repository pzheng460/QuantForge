import { useEffect, useRef, useCallback, useState } from 'react'
import clsx from 'clsx'
import { api, subscribeOptimize, subscribeAgent } from '../api/client'
import { useOptimizerStore } from '../stores/optimizerStore'
import { useCatalog } from '../hooks/useCatalog'
import AgentTraceViewer from '../components/AgentTraceViewer'
import MetricsSummary from '../components/MetricsSummary'
import type {
  OptimizeRequest,
  OptimizeJobStatus,
  StrategySchema,
  Exchange,
  GridSearchResult,
  AgentRunRequest,
  AgentJobStatus,
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
  const cls: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-700',
    running: 'bg-blue-100 text-blue-700 animate-pulse',
    completed: 'bg-green-100 text-green-700',
    failed: 'bg-red-100 text-red-700',
  }
  return (
    <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', cls[status] ?? 'bg-gray-100 text-gray-600')}>
      {status}
    </span>
  )
}

// ─── Grid Search Results ──────────────────────────────────────────────────────

function GridResults({ r }: { r: GridSearchResult }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-4 bg-indigo-50 rounded-lg">
        <div>
          <div className="text-xs text-gray-500">Best Sharpe</div>
          <div className="text-xl font-semibold text-indigo-700">{num(r.best_sharpe)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Best Return</div>
          <div className={clsx('text-xl font-semibold', r.best_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>
            {pct(r.best_return_pct)}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Max Drawdown</div>
          <div className="text-xl font-semibold text-red-500">{pct(r.best_drawdown_pct, false)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Train Period</div>
          <div className="text-sm font-medium text-gray-700">{r.train_start} → {r.train_end}</div>
        </div>
      </div>

      <div>
        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Best Parameters</div>
        <div className="flex flex-wrap gap-2">
          {Object.entries(r.best_params).map(([k, v]) => (
            <span key={k} className="text-xs bg-gray-100 rounded px-2 py-1">
              <span className="text-gray-500">{k}:</span> <span className="font-medium">{String(v)}</span>
            </span>
          ))}
        </div>
      </div>

      <div>
        <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Top {r.rows.length} Combinations</div>
        <div className="overflow-x-auto">
          <table className="text-xs w-full">
            <thead>
              <tr className="border-b border-gray-100">
                {['Rank', 'Sharpe', 'Return', 'Drawdown', 'Trades', 'Win%', 'Parameters'].map(h => (
                  <th key={h} className="py-2 px-2 text-left text-gray-400 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {r.rows.map((row) => (
                <tr key={row.rank} className={clsx('border-b border-gray-50 hover:bg-gray-50', row.rank === 1 && 'bg-indigo-50/50')}>
                  <td className="py-1.5 px-2 text-gray-400">{row.rank}</td>
                  <td className={clsx('py-1.5 px-2 tabular-nums font-medium', row.sharpe >= 1 ? 'text-green-600' : 'text-gray-700')}>{num(row.sharpe)}</td>
                  <td className={clsx('py-1.5 px-2 tabular-nums', row.total_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>{pct(row.total_return_pct)}</td>
                  <td className="py-1.5 px-2 tabular-nums text-red-500">{pct(row.max_drawdown_pct, false)}</td>
                  <td className="py-1.5 px-2 tabular-nums">{row.total_trades}</td>
                  <td className="py-1.5 px-2 tabular-nums">{num(row.win_rate_pct, 1)}%</td>
                  <td className="py-1.5 px-2">
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(row.params).map(([k, v]) => (
                        <span key={k} className="bg-gray-100 rounded px-1">{k}={String(v)}</span>
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
    agentSkills, setAgentSkills,
    selectedSkill, setSelectedSkill,
    resetAgent,
  } = useOptimizerStore()

  const wsCleanupRef = useRef<(() => void) | null>(null)

  // Load agent skills
  useEffect(() => {
    api.agentSkills()
      .then(skills => {
        setAgentSkills(skills)
        if (skills.length > 0 && !selectedSkill) {
          setSelectedSkill(skills[0].name)
        }
      })
      .catch(console.error)
  }, [selectedSkill])

  // Set default strategy on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      setStrategy(strategies[0].name)
      setInitialized(true)
    }
  }, [strategies, initialized])

  // WebSocket subscription for grid search — reconnects on remount if job is still running
  useEffect(() => {
    if (!jobId || mode !== 'grid') return
    if (status === 'completed' || status === 'failed') return
    wsCleanupRef.current?.()
    wsCleanupRef.current = subscribeOptimize(
      jobId,
      (msg) => {
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
    if (agentStatus === 'completed' || agentStatus === 'failed') return

    const cleanup = subscribeAgent(
      agentJobId,
      (event) => {
        addAgentEvent(event)
      },
      (err) => { setAgentError(String(err)) }
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
      const agentJob = await api.runAgent(req)
      setAgentJobId(agentJob.job_id)
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
  const selectedSkillInfo = agentSkills.find(s => s.name === selectedSkill)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Optimizer</h1>

      {/* Config */}
      <div className="card space-y-5">
        <h2 className="text-sm font-semibold text-gray-700">Configuration</h2>

        {/* Mode selector */}
        <div>
          <label className="text-xs text-gray-500 block mb-2">Optimization Mode</label>
          <div className="grid grid-cols-2 gap-2">
            {MODES.map((m) => (
              <button
                key={m.value}
                onClick={() => setMode(m.value as typeof mode)}
                className={clsx(
                  'text-left p-3 rounded-lg border transition-colors',
                  mode === m.value
                    ? 'border-brand-500 bg-brand-50 text-brand-700'
                    : 'border-gray-200 hover:border-gray-300 text-gray-600',
                )}
              >
                <div className="font-medium text-sm">{m.label}</div>
                <div className="text-xs text-gray-400 mt-0.5">{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* AI skill selector (only for AI mode) */}
        {mode === 'ai' && (
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">AI Skill</label>
            <select className="input text-sm" value={selectedSkill} onChange={(e) => setSelectedSkill(e.target.value)}>
              {agentSkills.map((skill) => (
                <option key={skill.name} value={skill.name}>{skill.name}</option>
              ))}
            </select>
            {selectedSkillInfo && (
              <div className="text-xs text-gray-400 mt-1">{selectedSkillInfo.description}</div>
            )}
          </div>
        )}

        {/* Row: strategy + exchange + symbol + leverage */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Strategy</label>
            <select className="input text-sm" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              {strategies.map((s) => <option key={s.name} value={s.name}>{s.display_name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Exchange</label>
            <select className="input text-sm" value={exchange} onChange={(e) => setExchange(e.target.value)}>
              {exchanges.map((ex) => <option key={ex.id} value={ex.id}>{ex.name}</option>)}
            </select>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Symbol (default: {selectedExchange?.default_symbol ?? '…'})</label>
            <input type="text" className="input text-sm" placeholder={selectedExchange?.default_symbol ?? ''} value={symbol} onChange={(e) => setSymbol(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Leverage</label>
            <input type="number" className="input text-sm" min={1} max={20} step={1} value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} />
          </div>
        </div>

        {/* Period / date range (only for grid search) */}
        {mode === 'grid' && (
          <div className="flex flex-wrap gap-4 items-end">
            <label className="flex items-center gap-2 cursor-pointer text-sm text-gray-600">
              <input type="checkbox" checked={useDateRange} onChange={(e) => setUseDateRange(e.target.checked)} className="w-4 h-4 rounded border-gray-300" />
              Custom date range
            </label>
            {!useDateRange ? (
              <div className="flex flex-col gap-1">
                <label className="text-xs text-gray-500">Period</label>
                <select className="input text-sm" value={period} onChange={(e) => setPeriod(e.target.value)}>
                  {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-1"><label className="text-xs text-gray-500">Start date</label><input type="date" className="input text-sm" value={startDate} onChange={(e) => setStartDate(e.target.value)} /></div>
                <div className="flex flex-col gap-1"><label className="text-xs text-gray-500">End date</label><input type="date" className="input text-sm" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></div>
              </>
            )}
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">Parallel jobs</label>
              <select className="input text-sm w-24" value={nJobs} onChange={(e) => setNJobs(Number(e.target.value))}>
                {[1, 2, 4, 8, -1].map((n) => <option key={n} value={n}>{n === -1 ? 'All CPUs' : n}</option>)}
              </select>
            </div>
          </div>
        )}

        <div className="flex items-center gap-4 pt-1">
          <button
            className="btn-primary"
            onClick={mode === 'grid' ? handleGridRun : handleAIRun}
            disabled={
              (mode === 'grid' && ((status === 'pending' || status === 'running') || !strategy)) ||
              (mode === 'ai' && (agentStatus === 'pending' || agentStatus === 'running' || !strategy || !selectedSkill))
            }
          >
            {mode === 'grid' ? (
              (status === 'pending' || status === 'running') ? 'Running…' : 'Run Grid Search'
            ) : (
              (agentStatus === 'pending' || agentStatus === 'running') ? 'Running…' : 'Run AI Optimize'
            )}
          </button>
          {((mode === 'grid' && (status === 'pending' || status === 'running')) ||
            (mode === 'ai' && (agentStatus === 'pending' || agentStatus === 'running'))) && (
            <button
              className="px-4 py-2 rounded text-sm font-semibold bg-red-100 text-red-600 hover:bg-red-200 transition-colors"
              onClick={handleCancel}
            >
              Cancel
            </button>
          )}
          {mode === 'grid' && status && <StatusBadge status={status} />}
          {mode === 'ai' && agentStatus && <StatusBadge status={agentStatus} />}
          {((mode === 'grid' && (status === 'pending' || status === 'running')) ||
            (mode === 'ai' && (agentStatus === 'pending' || agentStatus === 'running'))) && (
            <span className="text-xs text-gray-400">This may take several minutes…</span>
          )}
        </div>
      </div>

      {/* Error */}
      {error && mode === 'grid' && (
        <div className="card border border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700 mb-1">Grid search failed</p>
          <pre className="text-xs text-red-600 whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
        </div>
      )}

      {agentError && mode === 'ai' && (
        <div className="card border border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700 mb-1">AI optimization failed</p>
          <pre className="text-xs text-red-600 whitespace-pre-wrap overflow-auto max-h-48">{agentError}</pre>
        </div>
      )}

      {/* Results */}
      {mode === 'grid' && jobResult?.status === 'completed' && (
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">
            Results — <span className="font-normal text-gray-500">Grid Search · {strategy} · {exchange}</span>
          </h2>
          {jobResult.grid_result && <GridResults r={jobResult.grid_result} />}
        </div>
      )}

      {/* AI Trace Viewer */}
      {mode === 'ai' && agentJobId && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[600px]">
          <div className="lg:col-span-2 border border-tv-border rounded-lg bg-tv-panel">
            <AgentTraceViewer events={agentEvents} status={agentStatus} />
          </div>
          <div className="border border-tv-border rounded-lg bg-tv-panel">
            <MetricsSummary
              events={agentEvents}
              metrics={selectedSkillInfo?.metrics || []}
            />
          </div>
        </div>
      )}
    </div>
  )
}
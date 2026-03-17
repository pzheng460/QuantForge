import { useEffect, useState, useRef, useCallback } from 'react'
import clsx from 'clsx'
import { api, subscribeClosedLoop } from '../api/client'
import type {
  ClosedLoopRequest,
  ClosedLoopJobStatus,
  ClosedLoopIteration,
  FailureMode,
  ParameterChange,
  MathematicalReflection,
  HoldoutResult,
  StrategySchema,
  Exchange,
  BacktestResult,
} from '../types'
import TradingChart from '../components/chart/TradingChart'

// ─── Helper functions ──────────────────────────────────────────────────────

function pct(v: number, sign = true) {
  const s = sign && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}

function num(v: number, d = 2) {
  return v.toFixed(d)
}

function formatMetric(key: string, value: number): string {
  switch (key) {
    case 'total_return_pct':
    case 'max_drawdown_pct':
    case 'win_rate_pct':
      return pct(value)
    case 'sharpe_ratio':
    case 'profit_factor':
      return num(value)
    case 'total_trades':
      return value.toString()
    default:
      return num(value)
  }
}

// ─── Components ──────────────────────────────────────────────────────────────

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

function FailureBadge({ failure }: { failure: FailureMode }) {
  const severityColors = {
    low: 'bg-yellow-100 text-yellow-700 border-yellow-200',
    medium: 'bg-orange-100 text-orange-700 border-orange-200',
    high: 'bg-red-100 text-red-700 border-red-200',
  }

  return (
    <div className={clsx('inline-flex items-center gap-1 px-2 py-1 rounded border text-xs', severityColors[failure.severity])}>
      <span className="font-medium">{failure.type}</span>
      <span className="text-xs opacity-75">({failure.severity})</span>
    </div>
  )
}

function ParameterDiff({ change }: { change: ParameterChange }) {
  return (
    <div className="flex items-center gap-1 text-xs">
      <span className="text-tv-muted">{change.name}:</span>
      <span className="text-red-500">{change.before}</span>
      <span className="text-tv-muted">→</span>
      <span className="text-green-600">{change.after}</span>
      <span className="text-tv-border ml-1">({change.reason})</span>
    </div>
  )
}

function Gate1Criteria({ criteria }: { criteria: Record<string, boolean> }) {
  const items = [
    { key: 'profit_factor_gt_1_2', label: 'PF > 1.2' },
    { key: 'max_drawdown_lt_15', label: 'DD < 15%' },
    { key: 'win_rate_gt_30', label: 'WR > 30%' },
    { key: 'total_trades_gte_30', label: 'Trades ≥ 30' },
  ]

  return (
    <div className="flex flex-wrap gap-1">
      {items.map(({ key, label }) => (
        <span
          key={key}
          className={clsx(
            'text-xs px-1.5 py-0.5 rounded',
            criteria[key] ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'
          )}
        >
          {label}
        </span>
      ))}
    </div>
  )
}

function IterationCard({ iteration, isSelected, onClick }: {
  iteration: ClosedLoopIteration
  isSelected: boolean
  onClick: () => void
}) {
  const levelColors: Record<number, string> = {
    1: 'border-l-blue-500 bg-blue-50/20',
    2: 'border-l-orange-500 bg-orange-50/20',
    3: 'border-l-purple-500 bg-purple-50/20',
  }

  const improvement = iteration.improvement_pct
  const hasImprovement = improvement !== undefined && improvement > 0

  return (
    <div
      className={clsx(
        'border-l-4 p-4 rounded-r-lg cursor-pointer transition-all hover:shadow-sm',
        levelColors[iteration.level],
        isSelected ? 'bg-tv-blue/10 border-r-2 border-r-tv-blue' : 'bg-tv-panel'
      )}
      onClick={onClick}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">Iteration {iteration.iteration}</span>
          <span className="text-xs text-tv-muted">Level {iteration.level}</span>
          <StatusBadge status={iteration.status} />
        </div>
        {hasImprovement && (
          <span className="text-xs font-medium text-green-600">+{pct(improvement)}</span>
        )}
      </div>

      {/* Metrics comparison */}
      {iteration.metrics_before && (
        <div className="grid grid-cols-2 gap-2 mb-2 text-xs">
          <div>
            <div className="text-tv-muted">Return</div>
            <div className={clsx(iteration.metrics_before.total_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>
              {pct(iteration.metrics_before.total_return_pct)}
              {iteration.metrics_after && (
                <span className="text-tv-muted ml-1">
                  → {pct(iteration.metrics_after.total_return_pct)}
                </span>
              )}
            </div>
          </div>
          <div>
            <div className="text-tv-muted">Sharpe</div>
            <div>
              {num(iteration.metrics_before.sharpe_ratio)}
              {iteration.metrics_after && (
                <span className="text-tv-muted ml-1">
                  → {num(iteration.metrics_after.sharpe_ratio)}
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Failures */}
      {iteration.failures.length > 0 && (
        <div className="mb-2">
          <div className="text-xs text-tv-muted mb-1">Failures:</div>
          <div className="flex flex-wrap gap-1">
            {iteration.failures.slice(0, 3).map((f, i) => (
              <FailureBadge key={i} failure={f} />
            ))}
            {iteration.failures.length > 3 && (
              <span className="text-xs text-tv-muted">+{iteration.failures.length - 3} more</span>
            )}
          </div>
        </div>
      )}

      {/* Parameter changes */}
      {iteration.parameter_changes.length > 0 && (
        <div className="mb-2">
          <div className="text-xs text-tv-muted mb-1">Parameters:</div>
          <div className="space-y-0.5">
            {iteration.parameter_changes.slice(0, 2).map((c, i) => (
              <ParameterDiff key={i} change={c} />
            ))}
            {iteration.parameter_changes.length > 2 && (
              <div className="text-xs text-tv-muted">+{iteration.parameter_changes.length - 2} more changes</div>
            )}
          </div>
        </div>
      )}

      {/* Gate 1 status */}
      {iteration.gate1_pass !== undefined && (
        <div className="border-t border-tv-border pt-2">
          <div className="flex items-center justify-between">
            <span className="text-xs text-tv-muted">Gate 1</span>
            <span className={clsx('text-xs font-medium', iteration.gate1_pass ? 'text-green-600' : 'text-red-500')}>
              {iteration.gate1_pass ? 'PASS' : 'FAIL'}
            </span>
          </div>
          {iteration.gate1_criteria && (
            <Gate1Criteria criteria={iteration.gate1_criteria} />
          )}
        </div>
      )}
    </div>
  )
}

function IterationTimeline({ iterations, selectedIteration, onSelectIteration }: {
  iterations: ClosedLoopIteration[]
  selectedIteration: number | null
  onSelectIteration: (index: number) => void
}) {
  if (iterations.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-tv-muted">
        <div className="text-center">
          <div className="text-lg mb-2">⚙️</div>
          <div className="text-sm">No iterations yet</div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-4">
      {iterations.map((iteration, index) => (
        <div key={iteration.iteration} className="relative">
          {/* Connection line to next iteration */}
          {index < iterations.length - 1 && (
            <div className="absolute left-4 top-full w-0.5 h-4 bg-tv-border z-0" />
          )}

          <IterationCard
            iteration={iteration}
            isSelected={selectedIteration === index}
            onClick={() => onSelectIteration(index)}
          />
        </div>
      ))}
    </div>
  )
}

function SidePanel({ iteration, onClose }: {
  iteration: ClosedLoopIteration | null
  onClose: () => void
}) {
  if (!iteration) return null

  return (
    <div className="w-96 bg-tv-panel border-l border-tv-border flex flex-col">
      <div className="p-4 border-b border-tv-border flex items-center justify-between">
        <h3 className="text-sm font-semibold">Iteration {iteration.iteration} Details</h3>
        <button
          onClick={onClose}
          className="text-tv-muted hover:text-tv-text"
        >
          ✕
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Mathematical Reflection */}
        {iteration.mathematical_reflection && (
          <div>
            <h4 className="text-sm font-medium mb-2">Mathematical Reflection</h4>
            <div className="space-y-2 text-xs">
              <div>
                <div className="font-medium text-tv-muted">Risk Scenarios:</div>
                <ul className="list-disc list-inside space-y-1 mt-1">
                  {iteration.mathematical_reflection.risk_scenarios.map((scenario, i) => (
                    <li key={i} className="text-tv-text">{scenario}</li>
                  ))}
                </ul>
              </div>

              <div>
                <div className="font-medium text-tv-muted">Constraints:</div>
                <ul className="list-disc list-inside space-y-1 mt-1">
                  {iteration.mathematical_reflection.constraints.map((constraint, i) => (
                    <li key={i} className="text-tv-text">{constraint}</li>
                  ))}
                </ul>
              </div>

              <div>
                <div className="font-medium text-tv-muted">Reasoning:</div>
                <div className="mt-1 text-tv-text whitespace-pre-wrap">
                  {iteration.mathematical_reflection.reasoning}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Code Diff */}
        {iteration.pine_source_modified && (
          <div>
            <h4 className="text-sm font-medium mb-2">Pine Script Changes</h4>
            <div className="bg-tv-bg border border-tv-border rounded p-2 text-xs font-mono max-h-48 overflow-y-auto">
              <div className="text-green-600">// Modified Pine Script</div>
              <div className="text-tv-text whitespace-pre-wrap">
                {iteration.pine_source_modified.slice(0, 500)}
                {iteration.pine_source_modified.length > 500 && '...'}
              </div>
            </div>
          </div>
        )}

        {/* Detailed Failures */}
        {iteration.failures.length > 0 && (
          <div>
            <h4 className="text-sm font-medium mb-2">Failure Analysis</h4>
            <div className="space-y-2">
              {iteration.failures.map((failure, i) => (
                <div key={i} className="border border-tv-border rounded p-2">
                  <div className="flex items-center gap-2 mb-1">
                    <FailureBadge failure={failure} />
                  </div>
                  <div className="text-xs text-tv-text mb-1">{failure.detail}</div>
                  <div className="text-xs text-tv-muted">
                    <strong>Constraint:</strong> {failure.constraint_hint}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function SummaryBar({ jobStatus }: { jobStatus: ClosedLoopJobStatus }) {
  const hasResult = jobStatus.status === 'completed'
  const lastIteration = jobStatus.iterations[jobStatus.iterations.length - 1]
  const beforeMetrics = jobStatus.iterations[0]?.metrics_before
  const afterMetrics = lastIteration?.metrics_after || lastIteration?.metrics_before

  return (
    <div className="h-24 bg-tv-panel border-t border-tv-border p-4 flex items-center justify-between">
      <div className="flex items-center gap-8">
        <div>
          <div className="text-xs text-tv-muted">Iterations Used</div>
          <div className="text-lg font-semibold">
            {jobStatus.current_iteration}/{jobStatus.iterations.length > 0 ? 9 : '?'}
          </div>
        </div>

        <div>
          <div className="text-xs text-tv-muted">Current Level</div>
          <div className="text-lg font-semibold">
            Level {jobStatus.current_level}
          </div>
        </div>

        {jobStatus.final_verdict && (
          <div>
            <div className="text-xs text-tv-muted">Final Verdict</div>
            <div className={clsx(
              'text-sm font-semibold',
              jobStatus.final_verdict === 'converged' ? 'text-green-600' : 'text-orange-600'
            )}>
              {jobStatus.final_verdict.replace(/_/g, ' ')}
            </div>
          </div>
        )}

        {jobStatus.holdout_result && (
          <div>
            <div className="text-xs text-tv-muted">Gate 2 Holdout</div>
            <div className={clsx(
              'text-sm font-semibold',
              jobStatus.holdout_result.pass_gate2 ? 'text-green-600' : 'text-red-500'
            )}>
              {jobStatus.holdout_result.pass_gate2 ? 'PASS' : 'FAIL'}
            </div>
          </div>
        )}
      </div>

      {/* Before vs After comparison */}
      {beforeMetrics && afterMetrics && hasResult && (
        <div className="flex items-center gap-4">
          <div>
            <div className="text-xs text-tv-muted">Before → After</div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-tv-text">{pct(beforeMetrics.total_return_pct)}</span>
              <span className="text-tv-muted">→</span>
              <span className={clsx(
                'font-semibold',
                afterMetrics.total_return_pct > beforeMetrics.total_return_pct ? 'text-green-600' : 'text-red-500'
              )}>
                {pct(afterMetrics.total_return_pct)}
              </span>
            </div>
          </div>

          <div>
            <div className="text-xs text-tv-muted">Sharpe</div>
            <div className="flex items-center gap-2 text-sm">
              <span className="text-tv-text">{num(beforeMetrics.sharpe_ratio)}</span>
              <span className="text-tv-muted">→</span>
              <span className={clsx(
                'font-semibold',
                afterMetrics.sharpe_ratio > beforeMetrics.sharpe_ratio ? 'text-green-600' : 'text-red-500'
              )}>
                {num(afterMetrics.sharpe_ratio)}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Main Page Component ─────────────────────────────────────────────────────

export default function ClosedLoopPage() {
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])

  // Form state
  const [strategy, setStrategy] = useState('')
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('')
  const [timeframe, setTimeframe] = useState('1h')
  const [maxIterations, setMaxIterations] = useState(9)

  // Job state
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<ClosedLoopJobStatus | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // UI state
  const [selectedIteration, setSelectedIteration] = useState<number | null>(null)
  const [showSidePanel, setShowSidePanel] = useState(false)

  const wsCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    api.strategies().then(setStrategies)
    api.exchanges().then(setExchanges)
  }, [])

  // Set default strategy
  useEffect(() => {
    if (strategies.length > 0 && !strategy) {
      setStrategy(strategies[0].name)
    }
  }, [strategies, strategy])

  // WebSocket subscription
  useEffect(() => {
    if (!jobId) return

    wsCleanupRef.current?.()
    wsCleanupRef.current = subscribeClosedLoop(
      jobId,
      (status) => {
        setJobStatus(status)
        if (status.status === 'completed' || status.status === 'failed') {
          setLoading(false)
        }
      },
      (error) => {
        console.error('WebSocket error:', error)
        setError('Connection lost')
        setLoading(false)
      }
    )

    return () => wsCleanupRef.current?.()
  }, [jobId])

  const handleRun = useCallback(async () => {
    setLoading(true)
    setError(null)
    setJobStatus(null)
    setSelectedIteration(null)
    setShowSidePanel(false)

    try {
      const req: ClosedLoopRequest = {
        strategy,
        exchange,
        symbol: symbol || undefined,
        timeframe,
        max_iterations: maxIterations,
        period: '1y',
        warmup_days: 60,
      }

      const job = await api.runClosedLoop(req)
      setJobId(job.job_id)
      setJobStatus(job)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
  }, [strategy, exchange, symbol, timeframe, maxIterations])

  const handleSelectIteration = useCallback((index: number) => {
    setSelectedIteration(index)
    setShowSidePanel(true)
  }, [])

  const selectedIterationData = selectedIteration !== null && jobStatus
    ? jobStatus.iterations[selectedIteration]
    : null

  const selectedExchange = exchanges.find(e => e.id === exchange)

  return (
    <div className="flex flex-col h-full bg-tv-bg">
      {/* Configuration Panel */}
      <div className="shrink-0 bg-tv-panel border-b border-tv-border p-4">
        <div className="flex items-center justify-between mb-4">
          <h1 className="text-xl font-semibold text-tv-text">AI Closed-Loop Optimizer</h1>
          <div className="text-xs text-tv-muted">
            TiMi-style mathematical reflection → parameter optimization → validation
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 items-end">
          <div>
            <label className="text-xs text-tv-muted block mb-1">Strategy</label>
            <select
              className="tv-select text-xs"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
            >
              {strategies.map(s => (
                <option key={s.name} value={s.name}>{s.display_name}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-tv-muted block mb-1">Exchange</label>
            <select
              className="tv-select text-xs"
              value={exchange}
              onChange={(e) => setExchange(e.target.value)}
            >
              {exchanges.map(e => (
                <option key={e.id} value={e.id}>{e.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="text-xs text-tv-muted block mb-1">
              Symbol ({selectedExchange?.default_symbol})
            </label>
            <input
              type="text"
              className="tv-input text-xs"
              placeholder={selectedExchange?.default_symbol}
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
            />
          </div>

          <div>
            <label className="text-xs text-tv-muted block mb-1">Timeframe</label>
            <select
              className="tv-select text-xs"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            >
              <option value="1h">1h</option>
              <option value="4h">4h</option>
              <option value="1d">1d</option>
            </select>
          </div>

          <div>
            <label className="text-xs text-tv-muted block mb-1">Max Iterations</label>
            <input
              type="range"
              min="1"
              max="9"
              className="w-full"
              value={maxIterations}
              onChange={(e) => setMaxIterations(Number(e.target.value))}
            />
            <div className="text-xs text-tv-muted text-center mt-1">{maxIterations}</div>
          </div>
        </div>

        <div className="flex items-center gap-4 mt-4">
          <button
            onClick={handleRun}
            disabled={loading || !strategy}
            className="tv-btn-primary"
          >
            {loading ? 'Running...' : '🧠 Start AI Optimization'}
          </button>

          {jobStatus?.status && (
            <StatusBadge status={jobStatus.status} />
          )}

          {loading && (
            <span className="text-xs text-tv-muted animate-pulse">
              This may take several minutes...
            </span>
          )}
        </div>
      </div>

      {/* Error Display */}
      {error && (
        <div className="shrink-0 bg-red-50 border border-red-200 text-red-700 px-4 py-2 text-sm">
          <strong>Error:</strong> {error}
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex flex-1 min-h-0">
        {/* Iteration Timeline */}
        <IterationTimeline
          iterations={jobStatus?.iterations || []}
          selectedIteration={selectedIteration}
          onSelectIteration={handleSelectIteration}
        />

        {/* Side Panel */}
        {showSidePanel && (
          <SidePanel
            iteration={selectedIterationData}
            onClose={() => setShowSidePanel(false)}
          />
        )}
      </div>

      {/* Summary Bar */}
      {jobStatus && (
        <SummaryBar jobStatus={jobStatus} />
      )}
    </div>
  )
}
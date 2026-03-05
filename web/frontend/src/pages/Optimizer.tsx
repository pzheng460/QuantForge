import { useEffect, useState, useRef, useCallback } from 'react'
import clsx from 'clsx'
import { api, subscribeOptimize } from '../api/client'
import type {
  OptimizeRequest,
  OptimizeJobStatus,
  StrategySchema,
  Exchange,
  GridSearchResult,
  WFOResult,
  ThreeStageResult,
  HeatmapResult,
} from '../types'
import HeatmapChart from '../components/charts/HeatmapChart'

// ─── Helpers ─────────────────────────────────────────────────────────────────

const PERIODS = ['1m', '3m', '6m', '1y', '2y', '3y', '5y']
const MODES = [
  { value: 'grid', label: 'Grid Search', desc: 'Optimize params on 80% train data' },
  { value: 'wfo', label: 'Walk-Forward', desc: 'Rolling window out-of-sample test' },
  { value: 'full', label: 'Three-Stage', desc: 'Full in-sample → WFO → holdout pipeline' },
  { value: 'heatmap', label: 'Heatmap', desc: '2D parameter sensitivity scan' },
]

function pct(v: number, sign = true) {
  const s = sign && v > 0 ? '+' : ''
  return `${s}${v.toFixed(2)}%`
}
function num(v: number, d = 2) { return v.toFixed(d) }

function PassBadge({ pass }: { pass: boolean }) {
  return (
    <span className={clsx('text-xs font-bold px-2 py-0.5 rounded-full', pass ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600')}>
      {pass ? 'PASS' : 'FAIL'}
    </span>
  )
}

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

// ─── WFO Results ──────────────────────────────────────────────────────────────

function WFOResults({ r }: { r: WFOResult }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 p-4 bg-blue-50 rounded-lg">
        <div><div className="text-xs text-gray-500">Windows</div><div className="text-xl font-semibold">{r.windows_count}</div></div>
        <div><div className="text-xs text-gray-500">Avg Train Return</div><div className={clsx('text-xl font-semibold', r.avg_train_return >= 0 ? 'text-green-600' : 'text-red-500')}>{pct(r.avg_train_return)}</div></div>
        <div><div className="text-xs text-gray-500">Avg Test Return</div><div className={clsx('text-xl font-semibold', r.avg_test_return >= 0 ? 'text-green-600' : 'text-red-500')}>{pct(r.avg_test_return)}</div></div>
        <div>
          <div className="text-xs text-gray-500">Robustness Ratio</div>
          <div className={clsx('text-xl font-semibold', r.robustness_ratio >= 0.5 ? 'text-green-600' : 'text-red-500')}>{num(r.robustness_ratio)}</div>
          <div className="text-xs text-gray-400">≥0.5 = pass</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Positive Windows</div>
          <div className="text-xl font-semibold">{r.positive_windows}/{r.windows_count}</div>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="text-xs w-full">
          <thead>
            <tr className="border-b border-gray-100">
              {['Window', 'Train Period', 'Test Period', 'Best Params', 'Train Sharpe', 'Train Return', 'Test Sharpe', 'Test Return', 'Test DD'].map(h => (
                <th key={h} className="py-2 px-2 text-left text-gray-400 font-medium whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {r.windows.map((w) => (
              <tr key={w.window} className="border-b border-gray-50 hover:bg-gray-50">
                <td className="py-1.5 px-2 text-gray-400">{w.window + 1}</td>
                <td className="py-1.5 px-2 text-gray-500 whitespace-nowrap">{w.train_start} → {w.train_end}</td>
                <td className="py-1.5 px-2 text-gray-500 whitespace-nowrap">{w.test_start} → {w.test_end}</td>
                <td className="py-1.5 px-2">
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(w.best_params).map(([k, v]) => (
                      <span key={k} className="bg-gray-100 rounded px-1">{k}={String(v)}</span>
                    ))}
                  </div>
                </td>
                <td className={clsx('py-1.5 px-2 tabular-nums font-medium', w.train_sharpe >= 1 ? 'text-green-600' : 'text-gray-700')}>{num(w.train_sharpe)}</td>
                <td className={clsx('py-1.5 px-2 tabular-nums', w.train_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>{pct(w.train_return_pct)}</td>
                <td className={clsx('py-1.5 px-2 tabular-nums font-medium', w.test_sharpe >= 0.5 ? 'text-green-600' : 'text-red-500')}>{num(w.test_sharpe)}</td>
                <td className={clsx('py-1.5 px-2 tabular-nums', w.test_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>{pct(w.test_return_pct)}</td>
                <td className="py-1.5 px-2 tabular-nums text-red-500">{pct(w.test_drawdown_pct, false)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ─── Three-Stage Results ──────────────────────────────────────────────────────

function ThreeStageResults({ r }: { r: ThreeStageResult }) {
  const stages = [
    {
      label: 'Stage 1: In-Sample Optimization',
      pass: r.s1_pass,
      metrics: [
        { label: 'Sharpe', value: num(r.s1_in_sample_sharpe), good: r.s1_in_sample_sharpe >= 1 },
        { label: 'Return', value: pct(r.s1_in_sample_return), good: r.s1_in_sample_return > 0 },
        { label: 'Drawdown', value: pct(r.s1_in_sample_drawdown, false), good: false },
        { label: 'Trades', value: String(r.s1_in_sample_trades), good: r.s1_in_sample_trades >= 10 },
      ],
      note: `Sharpe ≥ 1.0 AND trades ≥ 10`,
    },
    {
      label: 'Stage 2: Walk-Forward Robustness',
      pass: r.s2_pass,
      metrics: [
        { label: 'Windows', value: String(r.s2_windows_count), good: null },
        { label: 'Robustness', value: num(r.s2_robustness_ratio), good: r.s2_robustness_ratio >= 0.5 },
        { label: 'Positive Windows', value: `${r.s2_positive_windows}/${r.s2_windows_count}`, good: r.s2_positive_windows / Math.max(r.s2_windows_count, 1) >= 0.5 },
        { label: 'Total Test Return', value: pct(r.s2_total_test_return), good: r.s2_total_test_return > 0 },
      ],
      note: `Robustness ≥ 0.5 AND positive windows ≥ 50%`,
    },
    {
      label: 'Stage 3: Holdout Test',
      pass: r.s3_pass,
      metrics: [
        { label: 'Return', value: pct(r.s3_holdout_return), good: r.s3_holdout_return > 0 },
        { label: 'B&H', value: pct(r.s3_bh_return), good: r.s3_holdout_return > r.s3_bh_return },
        {
          label: 'Sharpe',
          value: r.s3_sharpe_ci_lo != null ? `${num(r.s3_holdout_sharpe)} [${num(r.s3_sharpe_ci_lo)}, ${num(r.s3_sharpe_ci_hi!)}]` : num(r.s3_holdout_sharpe),
          good: r.s3_holdout_sharpe >= 0.5,
        },
        { label: 'Drawdown', value: pct(r.s3_holdout_drawdown, false), good: false },
        { label: 'Degradation', value: `${(r.s3_degradation * 100).toFixed(0)}%`, good: r.s3_degradation <= 0.5 },
      ],
      note: `Degradation ≤ 50% AND holdout Sharpe ≥ 0.5`,
    },
  ]

  return (
    <div className="space-y-4">
      {/* Overall verdict */}
      <div className={clsx('p-4 rounded-lg flex items-center gap-4', r.all_pass ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200')}>
        <div className={clsx('text-2xl font-bold', r.all_pass ? 'text-green-700' : 'text-red-600')}>
          {r.all_pass ? 'PASS' : 'FAIL'}
        </div>
        <div>
          <div className={clsx('font-medium', r.all_pass ? 'text-green-700' : 'text-red-600')}>
            {r.all_pass ? 'Strategy viable for live trading' : 'Strategy needs improvement'}
          </div>
          <div className="text-xs text-gray-500">Full-period B&H: {pct(r.bh_full_return)}</div>
        </div>
        <div className="ml-auto flex flex-wrap gap-2">
          {Object.entries(r.best_params).map(([k, v]) => (
            <span key={k} className="text-xs bg-white border border-gray-200 rounded px-2 py-0.5">
              {k}: <strong>{String(v)}</strong>
            </span>
          ))}
        </div>
      </div>

      {/* Stage cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {stages.map((s) => (
          <div key={s.label} className={clsx('card border', s.pass ? 'border-green-200' : 'border-red-200')}>
            <div className="flex items-start justify-between mb-3">
              <span className="text-xs font-semibold text-gray-700">{s.label}</span>
              <PassBadge pass={s.pass} />
            </div>
            <div className="space-y-1.5">
              {s.metrics.map((m) => (
                <div key={m.label} className="flex justify-between text-xs">
                  <span className="text-gray-500">{m.label}</span>
                  <span className={clsx('font-medium tabular-nums', m.good === true ? 'text-green-600' : m.good === false ? 'text-red-500' : 'text-gray-700')}>
                    {m.value}
                  </span>
                </div>
              ))}
            </div>
            <div className="mt-3 text-[10px] text-gray-400 border-t border-gray-100 pt-2">{s.note}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function OptimizerPage() {
  const [strategies, setStrategies] = useState<StrategySchema[]>([])
  const [exchanges, setExchanges] = useState<Exchange[]>([])

  const [strategy, setStrategy] = useState('')
  const [exchange, setExchange] = useState('bitget')
  const [symbol, setSymbol] = useState('')
  const [useDateRange, setUseDateRange] = useState(false)
  const [period, setPeriod] = useState('1y')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [leverage, setLeverage] = useState(1)
  const [mode, setMode] = useState<'grid' | 'wfo' | 'full' | 'heatmap'>('grid')
  const [nJobs, setNJobs] = useState(1)
  const [resolution, setResolution] = useState(15)

  const [jobId, setJobId] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [jobResult, setJobResult] = useState<OptimizeJobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const wsCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    api.strategies().then((d) => { setStrategies(d); if (d.length) setStrategy(d[0].name) })
    api.exchanges().then(setExchanges)
  }, [])

  useEffect(() => {
    if (!jobId) return
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
  }, [jobId])

  const handleRun = useCallback(async () => {
    setLoading(true)
    setJobResult(null)
    setError(null)
    setStatus('pending')

    const req: OptimizeRequest = {
      strategy, exchange,
      symbol: symbol || undefined,
      leverage, mode, n_jobs: nJobs, resolution,
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
  }, [strategy, exchange, symbol, leverage, mode, nJobs, resolution, useDateRange, period, startDate, endDate])

  const selectedExchange = exchanges.find((e) => e.id === exchange)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold text-gray-900">Optimizer</h1>

      {/* Config */}
      <div className="card space-y-5">
        <h2 className="text-sm font-semibold text-gray-700">Configuration</h2>

        {/* Mode selector */}
        <div>
          <label className="text-xs text-gray-500 block mb-2">Optimization Mode</label>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
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

        {/* Period / date range */}
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
          {mode === 'heatmap' && (
            <div className="flex flex-col gap-1">
              <label className="text-xs text-gray-500">Resolution</label>
              <input type="number" className="input text-sm w-24" min={5} max={30} step={1} value={resolution} onChange={(e) => setResolution(Number(e.target.value))} />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Parallel jobs</label>
            <select className="input text-sm w-24" value={nJobs} onChange={(e) => setNJobs(Number(e.target.value))}>
              {[1, 2, 4, 8, -1].map((n) => <option key={n} value={n}>{n === -1 ? 'All CPUs' : n}</option>)}
            </select>
          </div>
        </div>

        <div className="flex items-center gap-4 pt-1">
          <button className="btn-primary" onClick={handleRun} disabled={loading || !strategy}>
            {loading ? 'Running…' : 'Run Optimization'}
          </button>
          {status && <StatusBadge status={status} />}
          {loading && <span className="text-xs text-gray-400">This may take several minutes…</span>}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="card border border-red-200 bg-red-50">
          <p className="text-sm font-medium text-red-700 mb-1">Optimization failed</p>
          <pre className="text-xs text-red-600 whitespace-pre-wrap overflow-auto max-h-48">{error}</pre>
        </div>
      )}

      {/* Results */}
      {jobResult?.status === 'completed' && (
        <div className="card">
          <h2 className="text-sm font-semibold text-gray-700 mb-4">
            Results — <span className="font-normal text-gray-500">{jobResult.mode} · {strategy} · {exchange}</span>
          </h2>

          {jobResult.grid_result && <GridResults r={jobResult.grid_result} />}
          {jobResult.wfo_result && <WFOResults r={jobResult.wfo_result} />}
          {jobResult.full_result && <ThreeStageResults r={jobResult.full_result} />}
          {jobResult.heatmap_result && <HeatmapChart data={jobResult.heatmap_result} />}
        </div>
      )}
    </div>
  )
}

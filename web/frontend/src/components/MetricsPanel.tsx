import type { BacktestResult } from '../types'
import clsx from 'clsx'

interface Props {
  result: BacktestResult
}

function Metric({
  label,
  value,
  positive,
  negative,
  suffix = '',
  sub,
}: {
  label: string
  value: string
  positive?: boolean
  negative?: boolean
  suffix?: string
  sub?: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-gray-500">{label}</span>
      <span
        className={clsx('text-lg font-semibold tabular-nums', {
          'text-green-600': positive,
          'text-red-500': negative,
          'text-gray-900': !positive && !negative,
        })}
      >
        {value}
        {suffix && <span className="text-sm font-normal text-gray-500 ml-0.5">{suffix}</span>}
      </span>
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  )
}

function pct(v: number, digits = 2) {
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function num(v: number, digits = 2) {
  return v.toFixed(digits)
}

export default function MetricsPanel({ result }: Props) {
  const sharpeSub =
    result.sharpe_ci_lo != null && result.sharpe_ci_hi != null
      ? `95% CI [${result.sharpe_ci_lo.toFixed(2)}, ${result.sharpe_ci_hi.toFixed(2)}]`
      : undefined

  return (
    <div className="space-y-6">
      {/* Returns row */}
      <div>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Returns</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <Metric
            label="Total Return"
            value={pct(result.total_return_pct)}
            positive={result.total_return_pct > 0}
            negative={result.total_return_pct < 0}
          />
          <Metric
            label="B&H Return"
            value={pct(result.bh_return_pct)}
            positive={result.bh_return_pct > 0}
            negative={result.bh_return_pct < 0}
          />
          <Metric
            label="Alpha"
            value={pct(result.total_return_pct - result.bh_return_pct)}
            positive={result.total_return_pct > result.bh_return_pct}
            negative={result.total_return_pct < result.bh_return_pct}
          />
          <Metric
            label="Annualized Return"
            value={pct(result.annualized_return_pct)}
            positive={result.annualized_return_pct > 0}
            negative={result.annualized_return_pct < 0}
          />
        </div>
      </div>

      {/* Risk row */}
      <div>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Risk</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <Metric
            label="Max Drawdown"
            value={pct(result.max_drawdown_pct)}
            negative={true}
          />
          <Metric
            label="Sharpe Ratio"
            value={num(result.sharpe_ratio)}
            positive={result.sharpe_ratio >= 1}
            negative={result.sharpe_ratio < 0}
            sub={sharpeSub}
          />
          <Metric
            label="Sortino Ratio"
            value={num(result.sortino_ratio)}
            positive={result.sortino_ratio >= 1}
            negative={result.sortino_ratio < 0}
          />
          <Metric
            label="Calmar Ratio"
            value={num(result.calmar_ratio)}
            positive={result.calmar_ratio >= 1}
            negative={result.calmar_ratio < 0}
          />
        </div>
      </div>

      {/* Trade stats */}
      <div>
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">Trade Statistics</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
          <Metric label="Total Trades" value={String(result.total_trades)} />
          <Metric
            label="Win Rate"
            value={pct(result.win_rate_pct, 1)}
            positive={result.win_rate_pct >= 50}
          />
          <Metric
            label="Profit Factor"
            value={num(result.profit_factor)}
            positive={result.profit_factor >= 1.5}
            negative={result.profit_factor < 1}
          />
          <Metric label="Expectancy" value={`$${result.expectancy.toFixed(2)}`} positive={result.expectancy > 0} negative={result.expectancy < 0} />
          <Metric label="Avg Win" value={`$${result.avg_win.toFixed(2)}`} positive={true} />
          <Metric label="Avg Loss" value={`$${result.avg_loss.toFixed(2)}`} negative={true} />
          <Metric label="Largest Win" value={`$${result.largest_win.toFixed(2)}`} positive={true} />
          <Metric label="Largest Loss" value={`$${result.largest_loss.toFixed(2)}`} negative={true} />
        </div>
      </div>

      {/* Final equity */}
      <div className="text-xs text-gray-400 border-t border-gray-100 pt-3 flex gap-6">
        <span>Final Equity: <strong className="text-gray-700">${result.final_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}</strong></span>
        <span>Period: <strong className="text-gray-700">{result.period_start.slice(0, 10)} → {result.period_end.slice(0, 10)}</strong></span>
        <span>Config: <strong className="text-gray-700">{result.config_name}</strong></span>
      </div>
    </div>
  )
}

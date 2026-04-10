import type { BacktestResult } from '../types'
import { cn } from '@/lib/utils'

interface Props {
  result: BacktestResult
}

function Metric({
  label,
  value,
  positive,
  negative,
  sub,
}: {
  label: string
  value: string
  positive?: boolean
  negative?: boolean
  sub?: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={cn('text-lg font-semibold tabular-nums', {
          'text-tv-green': positive,
          'text-tv-red': negative,
          'text-foreground': !positive && !negative,
        })}
      >
        {value}
      </span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
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

function fmtDuration(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`
  if (hours < 24) return `${num(hours, 1)}h`
  const days = hours / 24
  return `${num(days, 1)}d`
}

export default function MetricsPanel({ result }: Props) {
  return (
    <div className="space-y-6">
      {/* Returns row */}
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Returns</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
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
          <Metric
            label="Recovery Factor"
            value={num(result.recovery_factor)}
            positive={result.recovery_factor > 1}
            negative={result.recovery_factor < 0}
            sub="Return / Max DD"
          />
        </div>
      </div>

      {/* Risk row */}
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Risk</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
          <Metric
            label="Max Drawdown"
            value={pct(result.max_drawdown_pct)}
            negative
            sub={`Duration: ${num(result.max_dd_duration_days, 1)} days`}
          />
          <Metric
            label="Sharpe Ratio"
            value={num(result.sharpe_ratio)}
            positive={result.sharpe_ratio >= 1}
            negative={result.sharpe_ratio < 0}
            sub={
              result.sharpe_ci_lo != null && result.sharpe_ci_hi != null
                ? `95% CI [${result.sharpe_ci_lo.toFixed(2)}, ${result.sharpe_ci_hi.toFixed(2)}]`
                : undefined
            }
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
          <Metric
            label="Ann. Volatility"
            value={`${num(result.annualized_volatility_pct)}%`}
          />
        </div>
      </div>

      {/* Trade stats */}
      <div>
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-3">Trade Statistics</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
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
          <Metric
            label="Payoff Ratio"
            value={num(result.payoff_ratio)}
            positive={result.payoff_ratio > 1}
            negative={result.payoff_ratio < 1}
            sub="Avg Win / Avg Loss"
          />
          <Metric
            label="Expectancy"
            value={`$${result.expectancy.toFixed(2)}`}
            positive={result.expectancy > 0}
            negative={result.expectancy < 0}
          />
          <Metric label="Avg Win" value={`$${result.avg_win.toFixed(2)}`} positive />
          <Metric label="Avg Loss" value={`$${result.avg_loss.toFixed(2)}`} negative />
          <Metric label="Largest Win" value={`$${result.largest_win.toFixed(2)}`} positive />
          <Metric label="Largest Loss" value={`$${result.largest_loss.toFixed(2)}`} negative />
          <Metric
            label="Max Consec. Wins"
            value={String(result.max_consecutive_wins)}
            positive
          />
          <Metric
            label="Max Consec. Losses"
            value={String(result.max_consecutive_losses)}
            negative
          />
          <Metric
            label="Avg Trade Duration"
            value={fmtDuration(result.avg_trade_duration_hours)}
          />
        </div>
      </div>

      {/* Final equity footer */}
      <div className="text-xs text-muted-foreground border-t border-border pt-3 flex flex-wrap gap-6">
        <span>
          Final Equity:{' '}
          <span className="text-foreground font-medium">
            ${result.final_equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </span>
        </span>
        <span>
          Period:{' '}
          <span className="text-foreground font-medium">
            {result.period_start.slice(0, 10)} &rarr; {result.period_end.slice(0, 10)}
          </span>
        </span>
        <span>
          Config:{' '}
          <span className="text-foreground font-medium">{result.config_name}</span>
        </span>
      </div>
    </div>
  )
}

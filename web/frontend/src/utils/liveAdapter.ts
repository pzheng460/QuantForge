/**
 * Converts LivePerformance data to a BacktestResult-compatible shape
 * so the StrategyTester component can render it.
 */

import type {
  LivePerformance,
  BacktestResult,
  TradeRecord,
  EquityPoint,
  DrawdownPoint,
} from '../types'

export function livePerformanceToBacktestResult(
  perf: LivePerformance,
  meta?: { exchange?: string; strategy?: string }
): BacktestResult {
  const initial = perf.initial_balance || 100000

  // --- Trades ---
  const trades: TradeRecord[] = perf.trades.map((t) => ({
    timestamp: t.entry_time,
    side: t.side === 'long' ? 'buy' : 'sell',
    price: t.entry_price,
    exit_price: t.exit_price,
    amount: t.amount || 0,
    fee: 0,
    pnl: t.pnl,
    pnl_pct: t.pnl_pct,
    entry_time: t.entry_time,
    exit_time: t.exit_time,
  }))

  // --- Equity curve (one point per trade exit) ---
  const equity_curve: EquityPoint[] = [
    { t: perf.start_time || perf.last_update, strategy: initial, bh: initial },
  ]
  let running = initial
  for (const t of perf.trades) {
    running += t.pnl
    equity_curve.push({
      t: t.exit_time,
      strategy: running,
      bh: initial, // B&H not available in live mode
    })
  }

  // --- Drawdown curve ---
  const drawdown_curve: DrawdownPoint[] = []
  let peak = initial
  running = initial
  for (const t of perf.trades) {
    running += t.pnl
    if (running > peak) peak = running
    const dd = peak > 0 ? ((peak - running) / peak) * 100 : 0
    drawdown_curve.push({ t: t.exit_time, dd })
  }

  // --- Derived stats ---
  const winTrades = perf.trades.filter((t) => t.pnl > 0)
  const loseTrades = perf.trades.filter((t) => t.pnl <= 0)
  const avgWin = winTrades.length
    ? winTrades.reduce((s, t) => s + t.pnl, 0) / winTrades.length
    : 0
  const avgLoss = loseTrades.length
    ? loseTrades.reduce((s, t) => s + t.pnl, 0) / loseTrades.length
    : 0

  return {
    total_return_pct: perf.total_return_pct,
    bh_return_pct: 0,
    annualized_return_pct: 0,
    max_drawdown_pct: perf.max_drawdown_pct,
    max_dd_duration_days: 0,
    sharpe_ratio: 0,
    sharpe_ci_lo: null,
    sharpe_ci_hi: null,
    sortino_ratio: 0,
    calmar_ratio: 0,
    annualized_volatility_pct: 0,
    recovery_factor: 0,
    total_trades: perf.total_trades,
    win_rate_pct: perf.win_rate_pct,
    profit_factor: perf.profit_factor,
    payoff_ratio: avgLoss !== 0 ? Math.abs(avgWin / avgLoss) : 0,
    avg_win: avgWin,
    avg_loss: avgLoss,
    expectancy:
      perf.total_trades > 0 ? perf.total_pnl / perf.total_trades : 0,
    largest_win: winTrades.length
      ? Math.max(...winTrades.map((t) => t.pnl))
      : 0,
    largest_loss: loseTrades.length
      ? Math.min(...loseTrades.map((t) => t.pnl))
      : 0,
    max_consecutive_wins: 0,
    max_consecutive_losses: 0,
    avg_trade_duration_hours: 0,
    final_equity: perf.current_balance,
    initial_capital: initial,
    net_profit: perf.total_pnl,
    gross_profit: winTrades.reduce((s, t) => s + t.pnl, 0),
    gross_loss: loseTrades.reduce((s, t) => s + t.pnl, 0),
    commission_paid: 0,
    num_winning_trades: perf.winning_trades,
    num_losing_trades: perf.losing_trades,
    equity_curve,
    drawdown_curve,
    monthly_returns: [],
    trades,
    strategy: meta?.strategy || perf.config_name || '',
    exchange: meta?.exchange || '',
    period_start: perf.start_time,
    period_end: perf.last_update,
    config_name: perf.config_name || '',
  }
}

export interface SchemaField {
  name: string
  type: 'float' | 'int' | 'str' | 'bool'
  default: number | string | boolean | null
  label: string
  min?: number
  max?: number
  step?: number
}

export interface StrategySchema {
  name: string
  display_name: string
  default_interval: string
  config_fields: SchemaField[]
  filter_fields: SchemaField[]
}

export interface Exchange {
  id: string
  name: string
  default_symbol: string
  maker_fee: number
  taker_fee: number
}

export interface TradeRecord {
  timestamp: string
  side: string
  price: number
  amount: number
  fee: number
  pnl: number
  pnl_pct: number
}

export interface EquityPoint {
  t: string
  strategy: number
  bh: number
}

export interface DrawdownPoint {
  t: string
  dd: number
}

export interface MonthlyReturn {
  year: number
  month: number
  return: number
}

export interface BacktestResult {
  total_return_pct: number
  bh_return_pct: number
  annualized_return_pct: number
  max_drawdown_pct: number
  sharpe_ratio: number
  sharpe_ci_lo: number | null
  sharpe_ci_hi: number | null
  sortino_ratio: number
  calmar_ratio: number
  total_trades: number
  win_rate_pct: number
  profit_factor: number
  avg_win: number
  avg_loss: number
  expectancy: number
  largest_win: number
  largest_loss: number
  final_equity: number
  equity_curve: EquityPoint[]
  drawdown_curve: DrawdownPoint[]
  monthly_returns: MonthlyReturn[]
  trades: TradeRecord[]
  strategy: string
  exchange: string
  period_start: string
  period_end: string
  config_name: string
}

export interface JobStatus {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  error?: string
  result?: BacktestResult
}

export interface BacktestRequest {
  strategy: string
  exchange: string
  symbol?: string
  period?: string
  start_date?: string
  end_date?: string
  leverage?: number
  mesa_index?: number
  config_override?: Record<string, number | string | boolean>
  filter_override?: Record<string, number | string | boolean>
}

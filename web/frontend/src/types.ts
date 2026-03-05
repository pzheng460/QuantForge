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

// ─── Optimizer types ──────────────────────────────────────────────────────────

export interface OptimizeRequest {
  strategy: string
  exchange: string
  symbol?: string
  period?: string
  start_date?: string
  end_date?: string
  leverage?: number
  mode: 'grid' | 'wfo' | 'full' | 'heatmap'
  n_jobs?: number
  resolution?: number
}

export interface GridRow {
  rank: number
  params: Record<string, unknown>
  sharpe: number
  total_return_pct: number
  max_drawdown_pct: number
  total_trades: number
  win_rate_pct: number
}

export interface GridSearchResult {
  best_params: Record<string, unknown>
  best_sharpe: number
  best_return_pct: number
  best_drawdown_pct: number
  rows: GridRow[]
  train_start: string
  train_end: string
}

export interface WFOWindow {
  window: number
  train_start: string
  train_end: string
  test_start: string
  test_end: string
  best_params: Record<string, unknown>
  train_sharpe: number
  train_return_pct: number
  test_sharpe: number
  test_return_pct: number
  test_drawdown_pct: number
}

export interface WFOResult {
  windows: WFOWindow[]
  windows_count: number
  avg_train_return: number
  avg_test_return: number
  robustness_ratio: number
  positive_windows: number
  total_test_return: number
}

export interface ThreeStageResult {
  best_params: Record<string, unknown>
  s1_in_sample_return: number
  s1_in_sample_sharpe: number
  s1_in_sample_drawdown: number
  s1_in_sample_trades: number
  s1_pass: boolean
  s2_windows_count: number
  s2_avg_train_return: number
  s2_avg_test_return: number
  s2_robustness_ratio: number
  s2_positive_windows: number
  s2_total_test_return: number
  s2_pass: boolean
  s3_holdout_return: number
  s3_bh_return: number
  s3_holdout_sharpe: number
  s3_sharpe_ci_lo: number | null
  s3_sharpe_ci_hi: number | null
  s3_holdout_drawdown: number
  s3_holdout_trades: number
  s3_holdout_win_rate: number
  s3_degradation: number
  s3_pass: boolean
  all_pass: boolean
  bh_full_return: number
}

export interface HeatmapMesa {
  index: number
  center_x: number
  center_y: number
  avg_sharpe: number
  avg_return_pct: number
  stability: number
  area: number
  frequency_label: string
}

export interface HeatmapResult {
  x_values: number[]
  y_values: number[]
  x_label: string
  y_label: string
  x_param: string
  y_param: string
  sharpe_grid: (number | null)[][]
  return_grid: (number | null)[][]
  mesas: HeatmapMesa[]
}

export interface OptimizeJobStatus {
  job_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  error?: string
  mode?: string
  grid_result?: GridSearchResult
  wfo_result?: WFOResult
  full_result?: ThreeStageResult
  heatmap_result?: HeatmapResult
}

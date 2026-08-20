import { create } from 'zustand'
import type { BacktestResult } from '../types'

/** ISO date (YYYY-MM-DD, UTC) `days` days before today. */
function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

export interface StrategyParam {
  name: string
  type: 'int' | 'float'
  value: number
  title: string
  min?: number
  max?: number
  step?: number
}

interface BacktestState {
  // Form
  selectedStrategy: string
  strategyParams: StrategyParam[]
  exchange: string
  symbol: string
  timeframe: string
  startDate: string
  endDate: string
  warmupBars: number
  /** Optional quote-currency notional per trade. */
  positionSizeUsdt: number | undefined

  // Job
  jobId: string | null
  status: string
  result: BacktestResult | null
  error: string | null
  loading: boolean

  // Actions
  setSelectedStrategy: (v: string) => void
  setStrategyParams: (v: StrategyParam[] | ((prev: StrategyParam[]) => StrategyParam[])) => void
  setExchange: (v: string) => void
  setSymbol: (v: string) => void
  setTimeframe: (v: string) => void
  setStartDate: (v: string) => void
  setEndDate: (v: string) => void
  setWarmupBars: (v: number) => void
  setPositionSizeUsdt: (v: number | undefined) => void
  setJobId: (v: string | null) => void
  setStatus: (v: string) => void
  setResult: (v: BacktestResult | null) => void
  setError: (v: string | null) => void
  setLoading: (v: boolean) => void

  // Track if initial strategy has been loaded
  initialized: boolean
  setInitialized: (v: boolean) => void
}

export const useBacktestStore = create<BacktestState>((set) => ({
  selectedStrategy: '',
  strategyParams: [],
  exchange: 'bitget',
  symbol: 'BTC/USDT:USDT',
  timeframe: '1h',
  // Default to a rolling window instead of a fixed date pair so the UI never
  // defaults to stale/past history.
  startDate: isoDaysAgo(366),
  endDate: isoDaysAgo(1),
  warmupBars: 500,
  positionSizeUsdt: undefined,

  jobId: null,
  status: '',
  result: null,
  error: null,
  loading: false,

  initialized: false,
  setInitialized: (v) => set({ initialized: v }),

  setSelectedStrategy: (v) => set({ selectedStrategy: v }),
  setStrategyParams: (v) => set((state) => ({
    strategyParams: typeof v === 'function' ? v(state.strategyParams) : v,
  })),
  setExchange: (v) => set({ exchange: v }),
  setSymbol: (v) => set({ symbol: v }),
  setTimeframe: (v) => set({ timeframe: v }),
  setStartDate: (v) => set({ startDate: v }),
  setEndDate: (v) => set({ endDate: v }),
  setWarmupBars: (v) => set({ warmupBars: v }),
  setPositionSizeUsdt: (v) => set({ positionSizeUsdt: v }),
  setJobId: (v) => set({ jobId: v }),
  setStatus: (v) => set({ status: v }),
  setResult: (v) => set({ result: v }),
  setError: (v) => set({ error: v }),
  setLoading: (v) => set({ loading: v }),
}))

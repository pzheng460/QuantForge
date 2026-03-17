import { create } from 'zustand'
import type { BacktestResult } from '../types'

interface PineParam {
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
  source: string
  pineParams: PineParam[]
  exchange: string
  symbol: string
  timeframe: string
  startDate: string
  endDate: string
  warmupDays: number

  // Job
  jobId: string | null
  status: string
  result: BacktestResult | null
  error: string | null
  loading: boolean

  // Actions
  setSelectedStrategy: (v: string) => void
  setSource: (v: string | ((prev: string) => string)) => void
  setPineParams: (v: PineParam[] | ((prev: PineParam[]) => PineParam[])) => void
  setExchange: (v: string) => void
  setSymbol: (v: string) => void
  setTimeframe: (v: string) => void
  setStartDate: (v: string) => void
  setEndDate: (v: string) => void
  setWarmupDays: (v: number) => void
  setJobId: (v: string | null) => void
  setStatus: (v: string) => void
  setResult: (v: BacktestResult | null) => void
  setError: (v: string | null) => void
  setLoading: (v: boolean) => void

  // Track if initial strategy has been loaded
  initialized: boolean
  setInitialized: (v: boolean) => void
}

export const DEFAULT_PINE = `//@version=5
strategy("EMA Cross", overlay=true, initial_capital=100000)
fast_len = input.int(9, title="Fast EMA")
slow_len = input.int(21, title="Slow EMA")
fast_ema = ta.ema(close, fast_len)
slow_ema = ta.ema(close, slow_len)
if ta.crossover(fast_ema, slow_ema)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast_ema, slow_ema)
    strategy.close("Long")
`

export const CUSTOM_KEY = '__custom__'

export const useBacktestStore = create<BacktestState>((set) => ({
  selectedStrategy: CUSTOM_KEY,
  source: DEFAULT_PINE,
  pineParams: [],
  exchange: 'bitget',
  symbol: 'BTC/USDT:USDT',
  timeframe: '1h',
  startDate: '2026-01-01',
  endDate: '2026-03-12',
  warmupDays: 60,

  jobId: null,
  status: '',
  result: null,
  error: null,
  loading: false,

  initialized: false,
  setInitialized: (v) => set({ initialized: v }),

  setSelectedStrategy: (v) => set({ selectedStrategy: v }),
  setSource: (v) => set((state) => ({
    source: typeof v === 'function' ? v(state.source) : v,
  })),
  setPineParams: (v) => set((state) => ({
    pineParams: typeof v === 'function' ? v(state.pineParams) : v,
  })),
  setExchange: (v) => set({ exchange: v }),
  setSymbol: (v) => set({ symbol: v }),
  setTimeframe: (v) => set({ timeframe: v }),
  setStartDate: (v) => set({ startDate: v }),
  setEndDate: (v) => set({ endDate: v }),
  setWarmupDays: (v) => set({ warmupDays: v }),
  setJobId: (v) => set({ jobId: v }),
  setStatus: (v) => set({ status: v }),
  setResult: (v) => set({ result: v }),
  setError: (v) => set({ error: v }),
  setLoading: (v) => set({ loading: v }),
}))

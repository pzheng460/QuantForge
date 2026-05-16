import { create } from 'zustand'
import type { LivePerformance, LiveEngineOut } from '../types'

interface PineParam {
  name: string
  type: 'int' | 'float'
  value: number
  title: string
  min?: number
  max?: number
  step?: number
}

interface DashboardState {
  // Form
  selectedStrategy: string
  source: string
  pineParams: PineParam[]
  exchange: string
  symbol: string
  timeframe: string
  positionSize: number
  leverage: number
  warmupBars: number
  demo: boolean

  // Engine
  engines: LiveEngineOut[]
  starting: boolean
  startError: string | null

  // Live perf
  perf: LivePerformance | null
  wsConnected: boolean

  // Track if initial strategy has been loaded
  initialized: boolean
  setInitialized: (v: boolean) => void

  // Actions
  setSelectedStrategy: (v: string) => void
  setSource: (v: string | ((prev: string) => string)) => void
  setPineParams: (v: PineParam[] | ((prev: PineParam[]) => PineParam[])) => void
  setExchange: (v: string) => void
  setSymbol: (v: string) => void
  setTimeframe: (v: string) => void
  setPositionSize: (v: number) => void
  setLeverage: (v: number) => void
  setWarmupBars: (v: number) => void
  setDemo: (v: boolean) => void
  setEngines: (v: LiveEngineOut[]) => void
  setStarting: (v: boolean) => void
  setStartError: (v: string | null) => void
  setPerf: (v: LivePerformance | null) => void
  setWsConnected: (v: boolean) => void
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

export const useDashboardStore = create<DashboardState>((set) => ({
  selectedStrategy: CUSTOM_KEY,
  source: DEFAULT_PINE,
  pineParams: [],
  exchange: 'bitget',
  symbol: 'BTC/USDT:USDT',
  timeframe: '1h',
  positionSize: 100,
  leverage: 1,
  warmupBars: 500,
  demo: true,

  engines: [],
  starting: false,
  startError: null,

  perf: null,
  wsConnected: false,

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
  setPositionSize: (v) => set({ positionSize: v }),
  setLeverage: (v) => set({ leverage: v }),
  setWarmupBars: (v) => set({ warmupBars: v }),
  setDemo: (v) => set({ demo: v }),
  setEngines: (v) => set({ engines: v }),
  setStarting: (v) => set({ starting: v }),
  setStartError: (v) => set({ startError: v }),
  setPerf: (v) => set({ perf: v }),
  setWsConnected: (v) => set({ wsConnected: v }),
}))

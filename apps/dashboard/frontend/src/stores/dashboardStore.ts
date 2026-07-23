import { create } from 'zustand'
import type { LivePerformance, LiveEngineOut } from '../types'

export interface StrategyParam {
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
  strategyParams: StrategyParam[]
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
  setStrategyParams: (v: StrategyParam[] | ((prev: StrategyParam[]) => StrategyParam[])) => void
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

export const useDashboardStore = create<DashboardState>((set) => ({
  selectedStrategy: '',
  strategyParams: [],
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
  setStrategyParams: (v) => set((state) => ({
    strategyParams: typeof v === 'function' ? v(state.strategyParams) : v,
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

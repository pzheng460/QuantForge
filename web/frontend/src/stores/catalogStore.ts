import { create } from 'zustand'
import type { StrategySchema, Exchange } from '../types'

interface CatalogState {
  strategies: StrategySchema[]
  exchanges: Exchange[]
  loaded: boolean

  setStrategies: (v: StrategySchema[]) => void
  setExchanges: (v: Exchange[]) => void
  setLoaded: (v: boolean) => void
}

export const useCatalogStore = create<CatalogState>((set) => ({
  strategies: [],
  exchanges: [],
  loaded: false,

  setStrategies: (v) => set({ strategies: v }),
  setExchanges: (v) => set({ exchanges: v }),
  setLoaded: (v) => set({ loaded: v }),
}))

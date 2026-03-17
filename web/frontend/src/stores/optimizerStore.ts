import { create } from 'zustand'
import type { AgentEvent, AgentSkillInfo, OptimizeJobStatus } from '../types'

interface OptimizerState {
  // Form
  strategy: string
  exchange: string
  symbol: string
  useDateRange: boolean
  period: string
  startDate: string
  endDate: string
  leverage: number
  mode: 'grid' | 'ai'
  nJobs: number
  resolution: number

  // Grid Job
  jobId: string | null
  status: string
  jobResult: OptimizeJobStatus | null
  error: string | null
  loading: boolean

  // Agent Job (persisted across tab switches)
  agentJobId: string | null
  agentStatus: string
  agentEvents: AgentEvent[]
  agentError: string | null
  agentSkills: AgentSkillInfo[]
  selectedSkill: string

  // Track if initial strategy has been loaded
  initialized: boolean
  setInitialized: (v: boolean) => void

  // Actions
  setStrategy: (v: string) => void
  setExchange: (v: string) => void
  setSymbol: (v: string) => void
  setUseDateRange: (v: boolean) => void
  setPeriod: (v: string) => void
  setStartDate: (v: string) => void
  setEndDate: (v: string) => void
  setLeverage: (v: number) => void
  setMode: (v: 'grid' | 'ai') => void
  setNJobs: (v: number) => void
  setResolution: (v: number) => void
  setJobId: (v: string | null) => void
  setStatus: (v: string) => void
  setJobResult: (v: OptimizeJobStatus | null) => void
  setError: (v: string | null) => void
  setLoading: (v: boolean) => void
  setAgentJobId: (v: string | null) => void
  setAgentStatus: (v: string) => void
  addAgentEvent: (e: AgentEvent) => void
  setAgentEvents: (events: AgentEvent[]) => void
  setAgentError: (v: string | null) => void
  setAgentSkills: (v: AgentSkillInfo[]) => void
  setSelectedSkill: (v: string) => void
  resetAgent: () => void
}

export const useOptimizerStore = create<OptimizerState>((set) => ({
  strategy: '',
  exchange: 'bitget',
  symbol: '',
  useDateRange: false,
  period: '1y',
  startDate: '',
  endDate: '',
  leverage: 1,
  mode: 'grid',
  nJobs: 1,
  resolution: 15,

  jobId: null,
  status: '',
  jobResult: null,
  error: null,
  loading: false,

  agentJobId: null,
  agentStatus: '',
  agentEvents: [],
  agentError: null,
  agentSkills: [],
  selectedSkill: '',

  initialized: false,
  setInitialized: (v) => set({ initialized: v }),

  setStrategy: (v) => set({ strategy: v }),
  setExchange: (v) => set({ exchange: v }),
  setSymbol: (v) => set({ symbol: v }),
  setUseDateRange: (v) => set({ useDateRange: v }),
  setPeriod: (v) => set({ period: v }),
  setStartDate: (v) => set({ startDate: v }),
  setEndDate: (v) => set({ endDate: v }),
  setLeverage: (v) => set({ leverage: v }),
  setMode: (v) => set({ mode: v }),
  setNJobs: (v) => set({ nJobs: v }),
  setResolution: (v) => set({ resolution: v }),
  setJobId: (v) => set({ jobId: v }),
  setStatus: (v) => set({ status: v }),
  setJobResult: (v) => set({ jobResult: v }),
  setError: (v) => set({ error: v }),
  setLoading: (v) => set({ loading: v }),
  setAgentJobId: (v) => set({ agentJobId: v }),
  setAgentStatus: (v) => set({ agentStatus: v }),
  addAgentEvent: (e) => set((s) => {
    // Deduplicate by timestamp+type+tool_name to handle WS reconnection replays
    const isDup = s.agentEvents.some(
      existing => existing.timestamp === e.timestamp && existing.type === e.type && existing.tool_name === e.tool_name && existing.content === e.content
    )
    if (isDup) return s
    return { agentEvents: [...s.agentEvents, e] }
  }),
  setAgentEvents: (events) => set({ agentEvents: events }),
  setAgentError: (v) => set({ agentError: v }),
  setAgentSkills: (v) => set({ agentSkills: v }),
  setSelectedSkill: (v) => set({ selectedSkill: v }),
  resetAgent: () => set({ agentJobId: null, agentStatus: '', agentEvents: [], agentError: null }),
}))

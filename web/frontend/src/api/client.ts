import type {
  BacktestRequest,
  Exchange,
  JobStatus,
  StrategySchema,
  OptimizeRequest,
  OptimizeJobStatus,
  LivePerformance,
  LiveStrategyStatus,
  LiveStartRequest,
  LiveEngineOut,
  AgentRunRequest,
  AgentJobStatus,
  AgentSkillInfo,
  AgentEvent,
} from '../types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export const api = {
  strategies: (): Promise<StrategySchema[]> => get('/strategies'),
  strategySource: (name: string): Promise<{ source: string }> =>
    get(`/strategies/${name}/source`),
  exchanges: (): Promise<Exchange[]> => get('/exchanges'),

  runBacktest: (req: BacktestRequest): Promise<JobStatus> =>
    post('/backtest/run', req),

  getBacktestStatus: (jobId: string): Promise<JobStatus> =>
    get(`/backtest/${jobId}`),
  cancelBacktest: (jobId: string): Promise<void> =>
    post(`/backtest/cancel/${jobId}`, {}),

  runOptimize: (req: OptimizeRequest): Promise<OptimizeJobStatus> =>
    post('/optimize/run', req),
  getOptimizeStatus: (jobId: string): Promise<OptimizeJobStatus> =>
    get(`/optimize/${jobId}`),
  cancelOptimize: (jobId: string): Promise<void> =>
    post(`/optimize/cancel/${jobId}`, {}),


  liveStrategies: (): Promise<LiveStrategyStatus[]> => get('/live/strategies'),
  livePerformance: (): Promise<LivePerformance> => get('/live/performance'),

  // Live engine management
  startLive: (req: LiveStartRequest): Promise<LiveEngineOut> =>
    post('/live/start', req),
  stopLive: (engineId: string): Promise<LiveEngineOut> =>
    post(`/live/stop/${engineId}`, {}),
  liveEngines: (): Promise<LiveEngineOut[]> => get('/live/engines'),

  // Agent workflow management
  runAgent: (req: AgentRunRequest): Promise<AgentJobStatus> =>
    post('/agent/run', req),
  getAgentStatus: (jobId: string): Promise<AgentJobStatus> =>
    get(`/agent/${jobId}`),
  stopAgent: (jobId: string): Promise<void> =>
    post(`/agent/${jobId}/stop`, {}),
  agentSkills: (): Promise<AgentSkillInfo[]> => get('/agent/skills'),
}

/** Subscribe to an optimize job via WebSocket. Returns a cleanup function. */
export function subscribeOptimize(
  jobId: string,
  onMessage: (msg: OptimizeJobStatus) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws/optimize/${jobId}`)
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  if (onError) ws.onerror = onError
  return () => ws.close()
}


/** Subscribe to live performance updates via WebSocket. Returns a cleanup function. */
export function subscribeLivePerformance(
  onMessage: (msg: LivePerformance) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws/live/performance`)
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  if (onError) ws.onerror = onError
  return () => ws.close()
}

/** Subscribe to agent events via WebSocket. Returns a cleanup function. */
export function subscribeAgent(
  jobId: string,
  onMessage: (event: AgentEvent) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws/agent/${jobId}`)
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch {
      /* ignore malformed frames */
    }
  }
  if (onError) ws.onerror = onError
  return () => ws.close()
}
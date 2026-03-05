import type {
  BacktestRequest,
  Exchange,
  JobStatus,
  StrategySchema,
  OptimizeRequest,
  OptimizeJobStatus,
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
  exchanges: (): Promise<Exchange[]> => get('/exchanges'),

  runBacktest: (req: BacktestRequest): Promise<JobStatus> =>
    post('/backtest/run', req),

  getBacktestStatus: (jobId: string): Promise<JobStatus> =>
    get(`/backtest/${jobId}`),

  runOptimize: (req: OptimizeRequest): Promise<OptimizeJobStatus> =>
    post('/optimize/run', req),

  getOptimizeStatus: (jobId: string): Promise<OptimizeJobStatus> =>
    get(`/optimize/${jobId}`),
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

/** Subscribe to a backtest job via WebSocket. Returns a cleanup function. */
export function subscribeBacktest(
  jobId: string,
  onMessage: (msg: JobStatus) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${protocol}://${window.location.host}/api/ws/backtest/${jobId}`)
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

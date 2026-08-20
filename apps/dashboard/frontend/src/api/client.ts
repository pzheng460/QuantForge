import type {
  BacktestRequest,
  Exchange,
  JobStatus,
  StrategySchema,
  OptimizeRequest,
  OptimizeJobStatus,
  LivePerformance,
  LiveStartRequest,
  LiveEngineOut,
  GlobalRiskState,
} from '../types'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

const BASE = '/api'

/**
 * Optional API key for remote deployments (backend bound to 0.0.0.0 with
 * QUANTFORGE_API_KEY set). Stored by the operator in localStorage so the UI
 * can authenticate; localhost deployments do not need one.
 */
const apiKey = (): string => localStorage.getItem('qf_api_key') ?? ''

function authHeaders(): Record<string, string> {
  const key = apiKey()
  return key ? { 'X-API-Key': key } : {}
}

function authQuery(): string {
  const key = apiKey()
  return key ? `?api_key=${encodeURIComponent(key)}` : ''
}

async function parseErrorMessage(res: Response): Promise<string> {
  try {
    const json = await res.json()
    return json.detail ?? json.message ?? JSON.stringify(json)
  } catch {
    try {
      return await res.text()
    } catch {
      return res.statusText
    }
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal, headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, await parseErrorMessage(res))
  return res.json()
}

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) throw new ApiError(res.status, await parseErrorMessage(res))
  return res.json()
}

async function del<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE', signal, headers: authHeaders() })
  if (!res.ok) throw new ApiError(res.status, await parseErrorMessage(res))
  return res.json()
}

export const api = {
  strategies: (): Promise<StrategySchema[]> => get('/strategies'),
  exchanges: (): Promise<Exchange[]> => get('/exchanges'),
  schwabStatus: (): Promise<{ configured: boolean; authenticated: boolean; trading_authenticated?: boolean; market_data_authenticated?: boolean; account_selected?: boolean; detail?: string }> =>
    get('/brokers/schwab/status'),
  schwabAuthStart: (product: 'trading' | 'market_data'): Promise<{ authorization_url: string; product: string }> =>
    get(`/brokers/schwab/auth/start?product=${product}`),
  schwabAccounts: (): Promise<Array<{ account_hash: string; account_type: string; display_id: string }>> =>
    get('/brokers/schwab/accounts'),
  selectSchwabAccount: (accountHash: string): Promise<{ selected: boolean; account_hash: string }> =>
    post('/brokers/schwab/account', { account_hash: accountHash }),

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


  // Live engine management
  startLive: (req: LiveStartRequest): Promise<LiveEngineOut> =>
    post('/live/start', req),
  stopLive: (engineId: string): Promise<LiveEngineOut> =>
    post(`/live/stop/${engineId}`, {}),
  /** Permanently delete an archived engine from the history list. */
  deleteLive: (engineId: string): Promise<{ engine_id: string; deleted: boolean }> =>
    del(`/live/engines/${engineId}`),
  liveEngines: (): Promise<LiveEngineOut[]> => get('/live/engines'),
  globalRisk: (): Promise<GlobalRiskState> => get('/risk/global'),
  setGlobalRisk: (halted: boolean, reason = ''): Promise<GlobalRiskState> =>
    fetch(`${BASE}/risk/global`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ halted, reason }),
    }).then(async (res) => {
      if (!res.ok) throw new ApiError(res.status, await parseErrorMessage(res))
      return res.json()
    }),
  analyzeSchwabOptions: (request: Record<string, unknown>): Promise<{
    report: {
      action: string
      reasons: string[]
      contract_symbol?: string
      contracts: number
      limit_price?: number
      data_quality: string
    }
    report_path: string
  }> => post('/options/schwab/analyze', request),

}

/** Subscribe to an optimize job via WebSocket. Returns a cleanup function. */
export function subscribeOptimize(
  jobId: string,
  onMessage: (msg: OptimizeJobStatus) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(
    `${protocol}://${window.location.host}/api/ws/optimize/${jobId}${authQuery()}`
  )
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch (err) {
      console.warn('[ws:optimize] failed to parse message:', err)
    }
  }
  ws.onclose = (e) => {
    console.log(`[ws:optimize] closed (code=${e.code}, reason=${e.reason})`)
    // Close (including a failed upgrade / auth rejection) must also
    // disconnect the UI state — onerror alone doesn't fire on close.
    onError?.(e)
  }
  if (onError) ws.onerror = onError
  return () => {
    if (ws.readyState === WebSocket.OPEN) ws.close()
  }
}


/** Subscribe to live performance updates via WebSocket. Returns a cleanup function. */
export function subscribeLivePerformance(
  onMessage: (msg: LivePerformance) => void,
  onError?: (e: Event) => void
): () => void {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(
    `${protocol}://${window.location.host}/api/ws/live/performance${authQuery()}`
  )
  ws.onmessage = (e) => {
    try {
      onMessage(JSON.parse(e.data))
    } catch (err) {
      console.warn('[ws:live] failed to parse message:', err)
    }
  }
  ws.onclose = (e) => {
    console.log(`[ws:live] closed (code=${e.code}, reason=${e.reason})`)
    // Close (including a failed upgrade / auth rejection) must also
    // disconnect the UI state — onerror alone doesn't fire on close.
    onError?.(e)
  }
  if (onError) ws.onerror = onError
  return () => {
    if (ws.readyState === WebSocket.OPEN) ws.close()
  }
}

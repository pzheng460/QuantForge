import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type {
  BacktestRequest,
  LiveStartRequest,
  OptimizeRequest,
} from '../types'

// ─── Catalog queries (shared across pages) ─────────────────────────────────

export function useStrategies() {
  return useQuery({
    queryKey: ['strategies'],
    queryFn: () => api.strategies(),
  })
}

export function useExchanges() {
  return useQuery({
    queryKey: ['exchanges'],
    queryFn: () => api.exchanges(),
  })
}

// ─── Live engine queries ────────────────────────────────────────────────────

export function useLiveEngines() {
  return useQuery({
    queryKey: ['live-engines'],
    queryFn: () => api.liveEngines(),
    refetchInterval: 5000,
  })
}

export function useLiveAccount() {
  return useQuery({
    queryKey: ['live-account'],
    queryFn: () => api.liveAccount(),
    refetchInterval: 5000,
  })
}

export function useStartLive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (req: LiveStartRequest) => api.startLive(req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-engines'] })
    },
  })
}

export function useStopLive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.stopLive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-engines'] })
    },
  })
}

export function useDeleteLive() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.deleteLive(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['live-engines'] })
    },
  })
}

// ─── Backtest queries ───────────────────────────────────────────────────────

export function useBacktestStatus(jobId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['backtest', jobId],
    queryFn: () => api.getBacktestStatus(jobId!),
    enabled: !!jobId && enabled,
    refetchInterval: 1500,
  })
}

export function useRunBacktest() {
  return useMutation({
    mutationFn: (req: BacktestRequest) => api.runBacktest(req),
  })
}

export function useCancelBacktest() {
  return useMutation({
    mutationFn: (jobId: string) => api.cancelBacktest(jobId),
  })
}

// ─── Optimizer queries ──────────────────────────────────────────────────────

export function useRunOptimize() {
  return useMutation({
    mutationFn: (req: OptimizeRequest) => api.runOptimize(req),
  })
}

export function useCancelOptimize() {
  return useMutation({
    mutationFn: (jobId: string) => api.cancelOptimize(jobId),
  })
}

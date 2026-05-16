import { z } from 'zod'

export const liveStartSchema = z.object({
  strategy: z.string().min(1, 'Select a strategy'),
  exchange: z.string().min(1, 'Select an exchange'),
  symbol: z.string().min(1, 'Symbol is required'),
  timeframe: z.string(),
  positionSize: z.number().positive('Must be positive'),
  leverage: z.number().int().min(1).max(125),
  warmupBars: z.number().int().min(0),
  demo: z.boolean(),
})

export const backtestSchema = z.object({
  exchange: z.string().min(1, 'Select an exchange'),
  symbol: z.string().optional(),
  timeframe: z.string(),
  startDate: z.string().min(1, 'Start date required'),
  endDate: z.string().min(1, 'End date required'),
  warmupDays: z.number().int().min(0).max(365),
})

export const optimizeSchema = z.object({
  strategy: z.string().min(1, 'Select a strategy'),
  exchange: z.string().min(1, 'Select an exchange'),
  symbol: z.string().optional(),
  leverage: z.number().int().min(1).max(20),
})

export type LiveStartFormData = z.infer<typeof liveStartSchema>
export type BacktestFormData = z.infer<typeof backtestSchema>
export type OptimizeFormData = z.infer<typeof optimizeSchema>

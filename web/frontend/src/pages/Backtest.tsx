import { useEffect, useState, useRef, useCallback } from 'react'
import { ChevronDown, Activity, Loader2, Play, Square } from 'lucide-react'
import { api } from '../api/client'
import { useBacktestStore, CUSTOM_KEY, DEFAULT_PINE } from '../stores/backtestStore'
import { useCatalog } from '../hooks/useCatalog'
import type { BacktestRequest, StrategySchema, Exchange, JobStatus } from '../types'
import TradingChart from '../components/charts/TradingChart'
import StrategyTester from '../components/StrategyTester'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible'

// ─── Resizable bottom panel ──────────────────────────────────────────────────

function useResizablePanel(defaultHeight: number) {
  const [height, setHeight] = useState(defaultHeight)
  const isDragging = useRef(false)
  const startY = useRef(0)
  const startH = useRef(defaultHeight)

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    isDragging.current = true
    startY.current = e.clientY
    startH.current = height
    e.preventDefault()
  }, [height])

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!isDragging.current) return
      const delta = startY.current - e.clientY
      setHeight(Math.max(160, Math.min(600, startH.current + delta)))
    }
    const onUp = () => { isDragging.current = false }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { height, onMouseDown }
}

// ─── Pine parameter parsing ─────────────────────────────────────────────────

interface PineParam {
  name: string
  type: 'int' | 'float'
  value: number
  title: string
  min?: number
  max?: number
  step?: number
}

function parsePineParams(source: string): PineParam[] {
  const params: PineParam[] = []
  const re = /^(\w+)\s*=\s*input\.(int|float)\((.+)\)/gm
  let m
  while ((m = re.exec(source))) {
    const [, name, type, argsStr] = m
    const defMatch = argsStr.match(/^(-?\d+\.?\d*)/)
    if (!defMatch) continue
    const ptype = type as 'int' | 'float'
    const value = ptype === 'int' ? parseInt(defMatch[1]) : parseFloat(defMatch[1])
    const titleMatch = argsStr.match(/title\s*=\s*"([^"]*)"/)
    const title = titleMatch ? titleMatch[1] : name
    const minMatch = argsStr.match(/minval\s*=\s*(-?\d+\.?\d*)/)
    const min = minMatch ? parseFloat(minMatch[1]) : undefined
    const maxMatch = argsStr.match(/maxval\s*=\s*(-?\d+\.?\d*)/)
    const max = maxMatch ? parseFloat(maxMatch[1]) : undefined
    const stepMatch = argsStr.match(/step\s*=\s*(\d+\.?\d*)/)
    const step = stepMatch ? parseFloat(stepMatch[1]) : undefined
    params.push({ name, type: ptype, value, title, min, max, step })
  }
  return params
}

function updatePineParam(source: string, paramName: string, newValue: number): string {
  const re = new RegExp(
    `(${paramName}\\s*=\\s*input\\.(?:int|float)\\()(-?\\d+\\.?\\d*)`,
  )
  return source.replace(re, `$1${newValue}`)
}

// ─── Collapsible section ────────────────────────────────────────────────────

function Section({ title, children, defaultOpen = true, action }: {
  title: string
  children: React.ReactNode
  defaultOpen?: boolean
  action?: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-b border-border">
      <div className="flex items-center">
        <CollapsibleTrigger className="flex flex-1 items-center justify-between px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors">
          {title}
          <ChevronDown
            className={cn(
              'h-3 w-3 transition-transform duration-200',
              open && 'rotate-180',
            )}
          />
        </CollapsibleTrigger>
        {action && <div className="pr-3 shrink-0">{action}</div>}
      </div>
      <CollapsibleContent>
        <div className="px-3 pb-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}

// ─── Main Backtest page ─────────────────────────────────────────────────────

export default function BacktestPage() {
  const { strategies, exchanges } = useCatalog()

  // Zustand store (persists across tab switches)
  const {
    selectedStrategy, setSelectedStrategy,
    source, setSource,
    pineParams, setPineParams,
    exchange, setExchange,
    symbol, setSymbol,
    timeframe, setTimeframe,
    startDate, setStartDate,
    endDate, setEndDate,
    warmupDays, setWarmupDays,
    jobId, setJobId,
    status, setStatus,
    result, setResult,
    error, setError,
    loading, setLoading,
    initialized, setInitialized,
  } = useBacktestStore()

  // Resizable bottom panel
  const { height: bottomHeight, onMouseDown: onDragStart } = useResizablePanel(280)

  // Track whether source change is from param update (skip re-parse)
  const paramUpdateRef = useRef(false)

  // Set default strategy on first-ever load
  useEffect(() => {
    if (!initialized && strategies.length > 0) {
      // Don't auto-select -- start with empty state
      setInitialized(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategies, initialized])

  // When selected strategy changes, fetch its source
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleStrategyChange = useCallback((name: string) => {
    setSelectedStrategy(name)
    if (name === CUSTOM_KEY) {
      setSource(DEFAULT_PINE)
      setPineParams(parsePineParams(DEFAULT_PINE))
    } else {
      api.strategySource(name).then(({ source: src }) => {
        setSource(src)
        setPineParams(parsePineParams(src))
      })
    }
  }, [])

  // When source text changes directly, re-parse params
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleSourceChange = useCallback((newSource: string) => {
    setSource(newSource)
    if (paramUpdateRef.current) {
      paramUpdateRef.current = false
      return
    }
    setPineParams(parsePineParams(newSource))
  }, [])

  // When a param value is changed, update source text
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleParamChange = useCallback((paramName: string, newValue: number) => {
    paramUpdateRef.current = true
    setSource((prev: string) => updatePineParam(prev, paramName, newValue))
    setPineParams((prev: PineParam[]) =>
      prev.map((p) => (p.name === paramName ? { ...p, value: newValue } : p)),
    )
  }, [])

  // Poll job status -- skip if already done
  useEffect(() => {
    if (!jobId) return
    if (status === 'completed' || status === 'failed') return
    let cancelled = false
    const poll = async () => {
      while (!cancelled) {
        try {
          const job: JobStatus = await api.getBacktestStatus(jobId)
          if (cancelled) break
          setStatus(job.status)
          if (job.status === 'completed' && job.result) {
            setResult(job.result)
            setLoading(false)
            break
          } else if (job.status === 'failed') {
            setError(job.error ?? 'Unknown error')
            setLoading(false)
            break
          }
        } catch {
          // ignore transient errors
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    }
    poll()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  // Submit backtest
  const handleRun = useCallback(async () => {
    setLoading(true)
    setResult(null)
    setError(null)
    setStatus('pending')
    try {
      const req: BacktestRequest = {
        pine_source: source,
        exchange,
        symbol,
        timeframe,
        start_date: startDate,
        end_date: endDate,
        warmup_days: warmupDays,
      }
      const job = await api.runBacktest(req)
      setJobId(job.job_id)
    } catch (e) {
      setError(String(e))
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, exchange, symbol, timeframe, startDate, endDate, warmupDays])

  const handleCancel = useCallback(async () => {
    if (!jobId) return
    try {
      await api.cancelBacktest(jobId)
      setStatus('cancelled')
      setLoading(false)
    } catch { /* ignore */ }
  }, [jobId, setStatus, setLoading])

  const selectedExchange = exchanges.find((ex) => ex.id === exchange)

  return (
    <div className="flex h-full min-h-0">

        {/* ── Left unified panel ──────────────────────────────────────── */}
        <div className="w-80 shrink-0 flex flex-col bg-card border-r border-border h-full">

          {/* Header */}
          <div className="px-3 py-2 border-b border-border flex items-center justify-between shrink-0">
            <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
              Backtest
            </span>
          </div>

          {/* Scrollable content */}
          <div className="flex-1 overflow-y-auto min-h-0 flex flex-col">

            {/* Strategy selector */}
            <Section title="Strategy">
              <div className="space-y-1">
                <div className="flex flex-col gap-1">
                  <Label>Strategy</Label>
                  <Select value={selectedStrategy} onValueChange={handleStrategyChange}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="-- Select --" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={CUSTOM_KEY}>Custom Script</SelectItem>
                      {strategies.map((s: StrategySchema) => (
                        <SelectItem key={s.name} value={s.name}>{s.display_name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </Section>

            {/* Pine Script editor */}
            <Section title="Pine Script">
              <div className="flex flex-col gap-1">
                <Textarea
                  value={source}
                  onChange={(e) => handleSourceChange(e.target.value)}
                  className="text-[11px] font-mono resize-y"
                  spellCheck={false}
                  placeholder="Enter Pine Script code..."
                  rows={12}
                  style={{ minHeight: 120, maxHeight: 400 }}
                />
              </div>
            </Section>

            {/* Parameters (parsed from Pine source) */}
            {pineParams.length > 0 && (
              <Section title={`Parameters (${pineParams.length})`}>
                <div className="space-y-0">
                  {pineParams.map((p) => (
                    <div key={p.name} className="flex flex-col gap-0.5 py-1">
                      <Label>{p.title}</Label>
                      <Input
                        type="number"
                        className="text-xs h-8"
                        value={p.value}
                        step={p.step ?? (p.type === 'int' ? 1 : 0.01)}
                        min={p.min ?? undefined}
                        max={p.max ?? undefined}
                        onChange={(e) => {
                          const v = p.type === 'int'
                            ? parseInt(e.target.value)
                            : parseFloat(e.target.value)
                          if (!isNaN(v)) handleParamChange(p.name, v)
                        }}
                      />
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* Backtest settings */}
            <Section title="Settings">
              <div className="space-y-1">
                <div className="flex flex-col gap-0.5 py-1">
                  <Label>Exchange</Label>
                  <Select value={exchange} onValueChange={setExchange}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="Select exchange" />
                    </SelectTrigger>
                    <SelectContent>
                      {exchanges.map((ex: Exchange) => (
                        <SelectItem key={ex.id} value={ex.id}>{ex.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex flex-col gap-1 py-1">
                  <Label>
                    Symbol{' '}
                    <span className="text-border">
                      ({selectedExchange?.default_symbol ?? '\u2026'})
                    </span>
                  </Label>
                  <Input
                    type="text"
                    className="text-xs h-8"
                    placeholder={selectedExchange?.default_symbol ?? ''}
                    value={symbol}
                    onChange={(e) => setSymbol(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label>Timeframe</Label>
                  <Select value={timeframe} onValueChange={setTimeframe}>
                    <SelectTrigger className="text-xs h-8">
                      <SelectValue placeholder="Select timeframe" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="1m">1m</SelectItem>
                      <SelectItem value="5m">5m</SelectItem>
                      <SelectItem value="15m">15m</SelectItem>
                      <SelectItem value="1h">1h</SelectItem>
                      <SelectItem value="4h">4h</SelectItem>
                      <SelectItem value="1d">1d</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div className="flex flex-col gap-0.5 py-1">
                    <Label>Start Date</Label>
                    <Input
                      type="date"
                      className="text-xs h-8"
                      value={startDate}
                      onChange={(e) => setStartDate(e.target.value)}
                    />
                  </div>
                  <div className="flex flex-col gap-0.5 py-1">
                    <Label>End Date</Label>
                    <Input
                      type="date"
                      className="text-xs h-8"
                      value={endDate}
                      onChange={(e) => setEndDate(e.target.value)}
                    />
                  </div>
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label>Warmup Days</Label>
                  <Input
                    type="number"
                    className="text-xs h-8"
                    value={warmupDays}
                    min={0}
                    max={365}
                    onChange={(e) => setWarmupDays(Number(e.target.value))}
                  />
                </div>
              </div>
            </Section>
          </div>

          {/* Run / Cancel buttons */}
          <div className="shrink-0 border-t border-border p-3 space-y-2">
            {(status === 'pending' || status === 'running') && (
              <div
                className={cn(
                  'text-xs text-center capitalize',
                  status === 'running'
                    ? 'text-primary animate-pulse'
                    : 'text-muted-foreground',
                )}
              >
                {status === 'running' ? (
                  <span className="inline-flex items-center gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Running...
                  </span>
                ) : (
                  status
                )}
              </div>
            )}
            {status === 'pending' || status === 'running' ? (
              <Button
                variant="destructive"
                size="sm"
                className="w-full"
                onClick={handleCancel}
              >
                <Square className="mr-1.5 h-3 w-3" />
                Cancel
              </Button>
            ) : (
              <Button
                size="sm"
                className="w-full"
                onClick={handleRun}
                disabled={loading || !source.trim()}
              >
                {loading ? (
                  <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
                ) : (
                  <Play className="mr-1.5 h-3 w-3" />
                )}
                {loading ? 'Submitting...' : 'Run Backtest'}
              </Button>
            )}
          </div>
        </div>

        {/* ── Right: Chart area + bottom panel ────────────────────────── */}
        <div className="flex flex-col flex-1 min-w-0">

          {/* Chart area */}
          <div className="flex-1 min-h-0 bg-background relative">
            {result ? (
              <TradingChart
                equityCurve={result.equity_curve}
                trades={result.trades}
                height={undefined}
              />
            ) : (
              <div className="flex items-center justify-center h-full">
                {loading ? (
                  <div className="flex flex-col items-center gap-3 text-muted-foreground">
                    <Loader2 className="h-8 w-8 animate-spin text-primary" />
                    <span className="text-sm capitalize">{status || 'Loading...'}</span>
                  </div>
                ) : error ? (
                  <div className="max-w-lg px-6">
                    <p className="text-sm font-medium text-tv-red mb-2">Backtest failed</p>
                    <pre className="text-xs text-muted-foreground whitespace-pre-wrap overflow-auto max-h-48 bg-card border border-border rounded-sm p-3">
                      {error}
                    </pre>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-3 text-muted-foreground">
                    <Activity className="h-12 w-12 stroke-1" />
                    <span className="text-sm">Select a strategy or write Pine Script, then click Run Backtest</span>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Resize handle */}
          {result && (
            <div
              className="h-1 bg-border cursor-row-resize hover:bg-primary transition-colors"
              onMouseDown={onDragStart}
            />
          )}

          {/* Bottom results panel */}
          {result && (
            <div
              className="shrink-0 bg-card border-t border-border overflow-hidden"
              style={{ height: bottomHeight }}
            >
              <StrategyTester result={result} />
            </div>
          )}
        </div>    </div>
  )
}

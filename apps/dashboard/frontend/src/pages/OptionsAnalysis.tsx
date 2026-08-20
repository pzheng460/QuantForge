import { useState } from 'react'
import { api } from '../api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { FormField } from '@/components/ui/form-field'
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select'
import {
  Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset,
} from '@/components/ui/sidebar'
import { ResizableSidebarShell } from '@/components/ResizableSidebarShell'

// Trend states recognized by the covered-call manager and its backtester
// (quantforge/options). The dashboard pins the default but lets the user
// choose, instead of silently hardcoding one regime.
const TREND_STATES = ['强势上涨', '温和上涨', '横盘', '温和下跌', '强势下跌'] as const
type TrendState = (typeof TREND_STATES)[number]

interface AnalysisReport {
  action: string
  reasons: string[]
  contract_symbol?: string
  contracts: number
  limit_price?: number
  data_quality: string
}

export default function OptionsAnalysisPage() {
  const [ticker, setTicker] = useState('TSLA')
  const [earningsDate, setEarningsDate] = useState('')
  const [coreShares, setCoreShares] = useState(0)
  const [maximumCoveredRatio, setMaximumCoveredRatio] = useState(0.5)
  const [trendState, setTrendState] = useState<TrendState>('横盘')
  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [reportPath, setReportPath] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function analyze() {
    setLoading(true)
    setError(null)
    try {
      const value = await api.analyzeSchwabOptions({
        ticker,
        as_of: new Date().toISOString().slice(0, 10),
        minimum_core_shares: coreShares,
        maximum_covered_ratio: maximumCoveredRatio,
        trend_state: trendState,
        earnings_date: earningsDate || null,
        earnings_confirmed: Boolean(earningsDate),
      })
      setReport(value.report)
      setReportPath(value.report_path)
    } catch (reason) {
      setError(String(reason))
    } finally {
      setLoading(false)
    }
  }

  return (
    <ResizableSidebarShell storageKey="options-analysis">
      <Sidebar collapsible="none" className="border-r border-border">
        <SidebarHeader className="px-3 py-2 border-b border-border">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Options Daily Analysis
          </span>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Inputs</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="space-y-2">
                <FormField label="Ticker">
                  <Input
                    className="text-xs h-7"
                    value={ticker}
                    onChange={(e) => setTicker(e.target.value.toUpperCase())}
                  />
                </FormField>
                <FormField label="Trend State">
                  <Select value={trendState} onValueChange={(v) => setTrendState(v as TrendState)}>
                    <SelectTrigger className="text-xs h-7">
                      <SelectValue placeholder="Select trend" />
                    </SelectTrigger>
                    <SelectContent>
                      {TREND_STATES.map((state) => (
                        <SelectItem key={state} value={state}>{state}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormField>
                <FormField label="Maximum Covered Ratio">
                  <Input
                    type="number" min={0} max={1} step={0.05}
                    className="text-xs h-7"
                    value={maximumCoveredRatio}
                    onChange={(e) => setMaximumCoveredRatio(Number(e.target.value))}
                  />
                </FormField>
                <FormField label="Minimum Core Shares">
                  <Input
                    type="number" min={0}
                    className="text-xs h-7"
                    value={coreShares}
                    onChange={(e) => setCoreShares(Number(e.target.value))}
                  />
                </FormField>
                <FormField label="Confirmed Earnings Date (optional)">
                  <Input
                    type="date"
                    className="text-xs h-7"
                    value={earningsDate}
                    onChange={(e) => setEarningsDate(e.target.value)}
                  />
                </FormField>
                <Button
                  type="button" size="sm" className="w-full"
                  onClick={analyze}
                  disabled={loading}
                >
                  {loading ? 'Analyzing live chain…' : 'Analyze Live Chain'}
                </Button>
                {error && (
                  <div className="text-[10px] text-destructive break-words">{error}</div>
                )}
              </div>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>

      <SidebarInset className="flex flex-col min-w-0">
        <div className="flex-1 min-h-0 overflow-y-auto bg-background">
          <div className="p-6 max-w-xl mx-auto">
            {!report && (
              <div className="text-center text-muted-foreground/60 text-xs py-16">
                Analysis results appear here. This page reads the live Schwab option
                chain and runs the covered-call manager — it never places orders.
              </div>
            )}
            {report && (
              <div className="space-y-3">
                <div className="rounded border border-border p-4 space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold">{report.action}</span>
                    <Badge variant="outline" className="text-[9px]">{report.data_quality}</Badge>
                  </div>
                  <ul className="text-[11px] text-muted-foreground list-disc pl-4 space-y-1">
                    {report.reasons.map((reason, i) => <li key={i}>{reason}</li>)}
                  </ul>
                  {report.contract_symbol && (
                    <div className="font-mono text-[11px]">
                      {report.contract_symbol} × {report.contracts}
                      {report.limit_price != null ? ` @ ${report.limit_price}` : ''}
                    </div>
                  )}
                  {reportPath && (
                    <div className="text-[10px] text-muted-foreground/60 font-mono pt-2 border-t border-border">
                      report: {reportPath}
                    </div>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground/70">
                  Analysis only — automatic submissions go through the dedicated run-once
                  flow, which enforces the same hard risk gates before any order.
                </div>
              </div>
            )}
          </div>
        </div>
      </SidebarInset>
    </ResizableSidebarShell>
  )
}

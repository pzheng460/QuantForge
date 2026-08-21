import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api } from '../api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { FormField } from '@/components/ui/form-field'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from '@/components/ui/select'
import {
  Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent,
  SidebarGroupLabel, SidebarHeader, SidebarInset,
} from '@/components/ui/sidebar'
import { ResizableSidebarShell } from '@/components/ResizableSidebarShell'
import { RefreshCw, FileText } from 'lucide-react'
import { useLang } from '../i18n'

/* ───────────────────────── minimal markdown renderer ─────────────────────── */
const KIND_KEY: Record<string, string> = {
  crypto: 'research.kindCrypto', options: 'research.kindOptions', technical: 'research.kindTechnical',
}
const KIND_ORDER = ['crypto', 'options', 'technical'] as const

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function renderInline(s: string): string {
  return escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

/** Very small markdown renderer — headings, tables, lists, quotes, code,
 *  paragraphs. Enough for the DuckDB research reports (table-centric). */
function MarkdownView({ text }: { text: string }) {
  const lines = text.split('\n')
  const nodes: ReactNode[] = []
  let i = 0
  let key = 0
  const push = (n: ReactNode) => nodes.push(<div key={key++}>{n}</div>)

  while (i < lines.length) {
    const line = lines[i]
    if (line.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].startsWith('```')) { buf.push(lines[i]); i++ }
      i++ // closing fence
      push(<pre className="text-[10px] font-mono overflow-x-auto p-3 rounded bg-muted/40">{buf.join('\n')}</pre>)
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const level = h[1].length
      const Tag = ({ children }: { children: ReactNode }) =>
        level === 1 ? <h3 className="text-sm font-semibold mt-4">{children}</h3>
          : level >= 3 ? <h5 className="text-[11px] font-semibold mt-3 text-muted-foreground">{children}</h5>
          : <h4 className="text-xs font-semibold mt-3">{children}</h4>
      push(<Tag><span dangerouslySetInnerHTML={{ __html: renderInline(h[2]) }} /></Tag>)
      i++
      continue
    }
    if (line.trim() === '---') { i++; continue }
    if (line.trim().startsWith('|')) {
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        const cells = lines[i].trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
        // skip separator row |:---:|---|
        if (!cells.every((c) => /^:?-+:?$/.test(c.replace(/\s/g, '')))) rows.push(cells)
        i++
      }
      if (rows.length) {
        const [head, ...body] = rows
        push(
          <div className="overflow-x-auto">
            <table className="text-[10px] font-mono border-collapse w-full">
              <thead>
                <tr>
                  {head.map((c, j) => (
                    <th key={j} className="border border-border px-2 py-1 text-left bg-muted/30 whitespace-nowrap">
                      <span dangerouslySetInnerHTML={{ __html: renderInline(c) }} />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((r, ri) => (
                  <tr key={ri}>
                    {r.map((c, ci) => (
                      <td key={ci} className="border border-border px-2 py-0.5 whitespace-nowrap text-right"
                        style={{ textAlign: ci === 0 ? 'left' : 'right' }}>
                        <span dangerouslySetInnerHTML={{ __html: renderInline(c) }} />
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>,
        )
      }
      continue
    }
    if (line.trim().startsWith('>')) {
      const buf: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, '')); i++
      }
      push(<blockquote className="text-[10px] text-muted-foreground border-l-2 border-border pl-3 my-1">{buf.join(' ')}</blockquote>)
      continue
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) { items.push(lines[i].replace(/^\s*[-*]\s+/, '')); i++ }
      push(
        <ul className="text-[11px] text-muted-foreground list-disc pl-4 space-y-0.5 my-1">
          {items.map((it, j) => <li key={j} dangerouslySetInnerHTML={{ __html: renderInline(it) }} />)}
        </ul>,
      )
      continue
    }
    if (line.trim() === '') { i++; continue }
    push(<p className="text-[11px] text-muted-foreground my-1" dangerouslySetInnerHTML={{ __html: renderInline(line.trim()) }} />)
    i++
  }
  return <div className="space-y-1">{nodes}</div>
}

/* ─────────────────────── covered-call analyzer (legacy) ─────────────────── */
const TREND_STATES = ['强势上涨', '温和上涨', '横盘', '温和下跌', '强势下跌'] as const
type TrendState = (typeof TREND_STATES)[number]

const TREND_KEYS: Record<TrendState, string> = {
  '强势上涨': 'research.trendStrongUp',
  '温和上涨': 'research.trendMildUp',
  '横盘': 'research.trendFlat',
  '温和下跌': 'research.trendMildDown',
  '强势下跌': 'research.trendStrongDown',
}

interface AnalysisReport {
  action: string
  reasons: string[]
  contract_symbol?: string
  contracts: number
  limit_price?: number
  data_quality: string
}

function OptionAnalyzer() {
  const { t } = useLang()
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
    <div className="flex flex-col md:flex-row gap-6">
      <div className="w-full md:w-64 shrink-0 space-y-2">
        <FormField label={t('research.ticker')}>
          <Input className="text-xs h-7" value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())} />
        </FormField>
        <FormField label={t('research.trendState')}>
          <Select value={trendState} onValueChange={(v) => setTrendState(v as TrendState)}>
            <SelectTrigger className="text-xs h-7"><SelectValue /></SelectTrigger>
            <SelectContent>
              {TREND_STATES.map((s) => <SelectItem key={s} value={s}>{t(TREND_KEYS[s])}</SelectItem>)}
            </SelectContent>
          </Select>
        </FormField>
        <FormField label={t('research.maxCoveredRatio')}>
          <Input type="number" min={0} max={1} step={0.05} className="text-xs h-7"
            value={maximumCoveredRatio} onChange={(e) => setMaximumCoveredRatio(Number(e.target.value))} />
        </FormField>
        <FormField label={t('research.minCoreShares')}>
          <Input type="number" min={0} className="text-xs h-7"
            value={coreShares} onChange={(e) => setCoreShares(Number(e.target.value))} />
        </FormField>
        <FormField label={t('research.confirmedEarningsDate')}>
          <Input type="date" className="text-xs h-7"
            value={earningsDate} onChange={(e) => setEarningsDate(e.target.value)} />
        </FormField>
        <Button type="button" size="sm" className="w-full" onClick={analyze} disabled={loading}>
          {loading ? t('research.analyzing') : t('research.analyze')}
        </Button>
        {error && <div className="text-[10px] text-destructive break-words">{error}</div>}
      </div>

      <div className="flex-1 min-w-0">
        {!report && (
          <div className="text-center text-muted-foreground/60 text-xs py-16">
            {t('research.emptyAnalyzer')}
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
                  {t('research.reportLabel')}: {reportPath}
                </div>
              )}
            </div>
            <div className="text-[10px] text-muted-foreground/70">
              {t('research.analysisOnly')}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

/* ───────────────────────────── Research page ─────────────────────────────── */
type ReportPayload = Awaited<ReturnType<typeof api.researchReports>>

function DailyReports() {
  const { t, locale } = useLang()
  const [data, setData] = useState<ReportPayload | null>(null)
  const [kind, setKind] = useState<string>('crypto')
  const [busy, setBusy] = useState(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    try {
      setData(await api.researchReports())
    } catch (reason) {
      setData(null)
      console.warn('research reports fetch failed', reason)
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(async () => {
      try {
        const d = await api.researchReports()
        setData(d)
        if (!d.refreshing) { setBusy(false); clearInterval(t) }
      } catch { /* transient */ }
    }, 4000)
    return () => clearInterval(t)
  }, [load])

  async function refresh() {
    setBusy(true)
    try {
      await api.refreshResearch()
    } catch (reason) {
      setBusy(false)
      console.warn('refresh start failed', reason)
      return
    }
    // poll until refreshing resolves
    const t = setInterval(async () => {
      try {
        const d = await api.researchReports()
        setData(d)
        if (!d.refreshing) { setBusy(false); clearInterval(t) }
      } catch { /* transient */ }
    }, 4000)
    poll.current = t
  }

  useEffect(() => () => { if (poll.current) clearInterval(poll.current) }, [])

  const reports = data?.reports ?? []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <div className="text-xs font-semibold">{t('research.dailyTitle')}</div>
        {data?.refreshing || busy ? (
          <Badge variant="outline" className="text-[9px] animate-pulse">{t('research.refreshing')}</Badge>
        ) : (
          <Badge variant="secondary" className="text-[9px]">
            {data?.last_refresh ? `${t('research.updatedAt')} ${new Date(data.last_refresh).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US', { hour12: false })}` : t('research.notRefreshed')}
          </Badge>
        )}
        <Button size="sm" variant="outline" className="h-7 text-xs ml-auto"
          onClick={refresh} disabled={data?.refreshing || busy}>
          <RefreshCw className={`w-3 h-3 mr-1 ${busy ? 'animate-spin' : ''}`} /> {t('research.refresh')}
        </Button>
      </div>
      {data?.last_error && (
        <div className="text-[10px] text-destructive break-words">{t('research.refreshFailed')}: {data.last_error}</div>
      )}

      {reports.length === 0 && (
        <div className="text-center text-muted-foreground/60 text-xs py-16">
          {t('research.noReports')}
        </div>
      )}

      {reports.length > 0 && (
        <Tabs value={kind} onValueChange={setKind}>
          <TabsList className="h-7">
            {KIND_ORDER.map((k) => {
              const r = reports.find((x) => x.kind === k)
              return (
                <TabsTrigger key={k} value={k} className="text-[11px] h-7">
                  {t(KIND_KEY[k])}
                  {r ? <span className="ml-1 text-[9px] text-muted-foreground">{r.updated_at.slice(0, 10)}</span> : null}
                </TabsTrigger>
              )
            })}
          </TabsList>
          {reports.map((r) => (
            <TabsContent key={r.kind} value={r.kind} className="mt-3">
              <div className="rounded border border-border p-4">
                <div className="flex items-center gap-2 pb-2 mb-2 border-b border-border">
                  <FileText className="w-3 h-3 text-muted-foreground" />
                  <span className="text-[10px] font-mono text-muted-foreground">{r.name}</span>
                  <span className="text-[9px] text-muted-foreground/60 ml-auto">
                    {new Date(r.updated_at).toLocaleString(locale === 'zh' ? 'zh-CN' : 'en-US', { hour12: false })}
                  </span>
                </div>
                <MarkdownView text={r.markdown} />
              </div>
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  )
}

export default function ResearchPage() {
  const { t } = useLang()
  return (
    <ResizableSidebarShell>
      <Sidebar collapsible="none" className="border-r border-border">
        <SidebarHeader className="px-3 py-2 border-b border-border">
          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('research.sidebarTitle')}
          </span>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>{t('research.sidebarLabel')}</SidebarGroupLabel>
            <SidebarGroupContent>
              <div className="text-[10px] text-muted-foreground space-y-1.5 px-1">
                <p>{t('research.sidebarDesc1')}</p>
                <p>{t('research.sidebarDesc2')}</p>
                <p>{t('research.sidebarDesc3')}</p>
              </div>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
      </Sidebar>

      <SidebarInset className="flex flex-col min-w-0">
        <div className="flex-1 min-h-0 overflow-y-auto bg-background">
          <div className="p-6 max-w-5xl mx-auto">
            <Tabs defaultValue="reports" className="w-full">
              <TabsList className="h-8">
                <TabsTrigger value="reports" className="text-xs h-8">{t('research.tabReports')}</TabsTrigger>
                <TabsTrigger value="analyzer" className="text-xs h-8">{t('research.tabAnalyzer')}</TabsTrigger>
              </TabsList>
              <TabsContent value="reports" className="mt-4">
                <DailyReports />
              </TabsContent>
              <TabsContent value="analyzer" className="mt-4">
                <OptionAnalyzer />
              </TabsContent>
            </Tabs>
          </div>
        </div>
      </SidebarInset>
    </ResizableSidebarShell>
  )
}

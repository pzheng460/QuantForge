import { useMemo } from 'react'
import { cn } from '@/lib/utils'
import { ArrowUpDown } from 'lucide-react'
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from '@/components/ui/accordion'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { DataTable } from '@/components/ui/data-table'
import type { ColumnDef } from '@tanstack/react-table'
import type { BacktestResult, TradeRecord } from '../types'
import { useTimezone, fmtDateTz } from '../hooks/useTimezone'

// ─── Formatting helpers ─────────────────────────────────────────────────────

function fmtUsdt(v: number, digits = 2): string {
  return `${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`
}

function fmtSignUsdt(v: number, digits = 2): string {
  const sign = v > 0 ? '+' : ''
  return `${sign}${fmtUsdt(v, digits)}`
}

function fmtPct(v: number, digits = 2): string {
  return `${v.toFixed(digits)}%`
}

function fmtSignPct(v: number, digits = 2): string {
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(digits)}%`
}

function fmtPrice(v: number): string {
  return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtInt(v: number): string {
  return String(Math.round(v))
}

function cc(v: number): string {
  return v > 0 ? 'text-tv-green' : v < 0 ? 'text-tv-red' : 'text-foreground'
}

// ─── Stats computation ──────────────────────────────────────────────────────

interface SideStats {
  totalTrades: number
  totalOpenTrades: number
  winningTrades: number
  losingTrades: number
  percentProfitable: number
  netPnl: number
  netPnlPct: number
  grossProfit: number
  grossProfitPct: number
  grossLoss: number
  grossLossPct: number
  profitFactor: number
  commissionPaid: number
  expectedPayoff: number
  avgPnl: number
  avgPnlPct: number
  avgWinTrade: number
  avgWinPct: number
  avgLossTrade: number
  avgLossPct: number
  ratioAvgWinLoss: number
  largestWin: number
  largestWinPct: number
  largestWinAsGrossProfit: number
  largestLoss: number
  largestLossPct: number
  largestLossAsGrossLoss: number
  avgBarsInTrades: number
  avgBarsInWinning: number
  avgBarsInLosing: number
  cagr: number
  returnOnInitialCapital: number
  returnOnAccountSize: number
  netProfitAsLargestLoss: number
}

function computeSideStats(
  trades: TradeRecord[],
  initialCapital: number,
  periodYears: number,
  maxDd: number,
): SideStats {
  const total = trades.length
  const wins = trades.filter(t => t.pnl > 0)
  const losses = trades.filter(t => t.pnl <= 0)
  const netPnl = trades.reduce((s, t) => s + t.pnl, 0)
  const grossProfit = wins.reduce((s, t) => s + t.pnl, 0)
  const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0))
  const pf = grossLoss > 0 ? grossProfit / grossLoss : 0
  const avgWin = wins.length > 0 ? grossProfit / wins.length : 0
  const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0
  const avgPnl = total > 0 ? netPnl / total : 0
  const largestWin = wins.length > 0 ? Math.max(...wins.map(t => t.pnl)) : 0
  const largestLoss = losses.length > 0 ? Math.max(...losses.map(t => Math.abs(t.pnl))) : 0

  // Percentage calculations (per-trade pnl_pct)
  const avgWinPct = wins.length > 0 ? wins.reduce((s, t) => s + t.pnl_pct, 0) / wins.length : 0
  const avgLossPct = losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.pnl_pct, 0) / losses.length) : 0
  const avgPnlPct = total > 0 ? trades.reduce((s, t) => s + t.pnl_pct, 0) / total : 0
  const largestWinPct = wins.length > 0 ? Math.max(...wins.map(t => t.pnl_pct)) : 0
  const largestLossPct = losses.length > 0 ? Math.max(...losses.map(t => Math.abs(t.pnl_pct))) : 0

  // Bars
  const allBars = trades.filter(t => t.bars_held != null).map(t => t.bars_held!)
  const winBars = wins.filter(t => t.bars_held != null).map(t => t.bars_held!)
  const lossBars = losses.filter(t => t.bars_held != null).map(t => t.bars_held!)
  const avgBars = allBars.length > 0 ? allBars.reduce((a, b) => a + b, 0) / allBars.length : 0
  const avgWinBars = winBars.length > 0 ? winBars.reduce((a, b) => a + b, 0) / winBars.length : 0
  const avgLossBars = lossBars.length > 0 ? lossBars.reduce((a, b) => a + b, 0) / lossBars.length : 0

  const returnPct = initialCapital > 0 ? netPnl / initialCapital * 100 : 0
  const cagr = periodYears > 0 && initialCapital > 0
    ? (Math.pow((initialCapital + netPnl) / initialCapital, 1 / periodYears) - 1) * 100
    : 0

  return {
    totalTrades: total,
    totalOpenTrades: 0,
    winningTrades: wins.length,
    losingTrades: losses.length,
    percentProfitable: total > 0 ? wins.length / total * 100 : 0,
    netPnl,
    netPnlPct: returnPct,
    grossProfit,
    grossProfitPct: initialCapital > 0 ? grossProfit / initialCapital * 100 : 0,
    grossLoss,
    grossLossPct: initialCapital > 0 ? grossLoss / initialCapital * 100 : 0,
    profitFactor: pf,
    commissionPaid: 0,
    expectedPayoff: avgPnl,
    avgPnl,
    avgPnlPct,
    avgWinTrade: avgWin,
    avgWinPct,
    avgLossTrade: avgLoss,
    avgLossPct,
    ratioAvgWinLoss: avgLoss > 0 ? avgWin / avgLoss : 0,
    largestWin,
    largestWinPct,
    largestWinAsGrossProfit: grossProfit > 0 ? largestWin / grossProfit * 100 : 0,
    largestLoss,
    largestLossPct,
    largestLossAsGrossLoss: grossLoss > 0 ? largestLoss / grossLoss * 100 : 0,
    avgBarsInTrades: avgBars,
    avgBarsInWinning: avgWinBars,
    avgBarsInLosing: avgLossBars,
    cagr,
    returnOnInitialCapital: returnPct,
    returnOnAccountSize: maxDd > 0 ? netPnl / maxDd * 100 : 0,
    netProfitAsLargestLoss: largestLoss > 0 ? netPnl / largestLoss * 100 : 0,
  }
}


// ─── TV-style metric table row ──────────────────────────────────────────────

function Row({ label, all, long, short, showLongShort = true }: {
  label: string
  all: React.ReactNode
  long?: React.ReactNode
  short?: React.ReactNode
  showLongShort?: boolean
}) {
  return (
    <tr className="border-b border-border/20 hover:bg-secondary/5">
      <td className="py-2 px-3 text-[12px] text-foreground">{label}</td>
      <td className="py-2 px-3 text-[12px] text-right">{all}</td>
      {showLongShort && <td className="py-2 px-3 text-[12px] text-right">{long ?? ''}</td>}
      {showLongShort && <td className="py-2 px-3 text-[12px] text-right">{short ?? ''}</td>}
    </tr>
  )
}

function TableHeader({ showLongShort = true }: { showLongShort?: boolean }) {
  return (
    <thead>
      <tr className="border-b border-border/30">
        <th className="py-1.5 px-3 text-left text-[11px] text-muted-foreground font-medium">Metric</th>
        <th className="py-1.5 px-3 text-right text-[11px] text-muted-foreground font-medium">All</th>
        {showLongShort && <th className="py-1.5 px-3 text-right text-[11px] text-muted-foreground font-medium">Long</th>}
        {showLongShort && <th className="py-1.5 px-3 text-right text-[11px] text-muted-foreground font-medium">Short</th>}
      </tr>
    </thead>
  )
}

function SectionHeader({ title }: { title: string }) {
  return (
    <tr>
      <td colSpan={4} className="py-2 px-3 text-[12px] font-semibold text-foreground bg-secondary/10">{title}</td>
    </tr>
  )
}

/** Two-line cell: main value + sub value */
function V({ main, sub, color }: { main: string; sub?: string; color?: string }) {
  return (
    <div>
      <div className={cn('tabular-nums', color)}>{main}</div>
      {sub && <div className={cn('text-[10px] tabular-nums', color ?? 'text-muted-foreground')}>{sub}</div>}
    </div>
  )
}

// ─── Tab: Strategy Report (merged Overview + Performance, matches TV) ──────

function StrategyReportTab({ r }: { r: BacktestResult }) {
  const ic = r.initial_capital
  const trades = r.trades
  const longTrades = trades.filter(t => t.side === 'buy')
  const shortTrades = trades.filter(t => t.side === 'sell')

  // Period in years for CAGR
  const d0 = new Date(r.period_start).getTime()
  const d1 = new Date(r.period_end).getTime()
  const periodYears = Math.max((d1 - d0) / (365.25 * 86400000), 0.01)

  // Max drawdown (USDT) from equity curve
  let maxDdUsdt = 0
  let maxDdPct = r.max_drawdown_pct
  let peak = ic
  for (const pt of r.equity_curve) {
    if (pt.strategy > peak) peak = pt.strategy
    const dd = peak - pt.strategy
    if (dd > maxDdUsdt) maxDdUsdt = dd
  }
  if (maxDdUsdt === 0 && maxDdPct > 0) {
    maxDdUsdt = maxDdPct / 100 * ic
  }
  const maxDdPctCalc = peak > 0 ? maxDdUsdt / peak * 100 : 0

  const all = useMemo(() => computeSideStats(trades, ic, periodYears, maxDdUsdt), [trades, ic, periodYears, maxDdUsdt])
  const lng = useMemo(() => computeSideStats(longTrades, ic, periodYears, maxDdUsdt), [longTrades, ic, periodYears, maxDdUsdt])
  const sht = useMemo(() => computeSideStats(shortTrades, ic, periodYears, maxDdUsdt), [shortTrades, ic, periodYears, maxDdUsdt])

  // Buy & hold
  const bhReturn = r.bh_return_pct
  const bhReturnUsdt = ic * bhReturn / 100
  const strategyOutperformance = all.netPnl - bhReturnUsdt

  // Equity run-ups and drawdowns
  const eqVals = r.equity_curve.map(p => p.strategy)
  const { runups, drawdowns } = useMemo(() => computeRunupsDrawdowns(eqVals, ic), [eqVals, ic])

  return (
    <div className="space-y-0">
      {/* ── Top KPI summary bar ── */}
      <div className="grid grid-cols-5 gap-4 py-3 px-1 border-b border-border/30">
        <div>
          <div className="text-[10px] text-muted-foreground uppercase">Total P&L</div>
          <div className={cn('text-sm font-semibold tabular-nums', cc(all.netPnl))}>
            {fmtSignUsdt(all.netPnl)}
          </div>
          <div className={cn('text-[10px] tabular-nums', cc(all.netPnl))}>{fmtSignPct(all.netPnlPct)}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase">Max equity drawdown</div>
          <div className="text-sm font-semibold tabular-nums text-foreground">
            {fmtUsdt(maxDdUsdt)}
          </div>
          <div className="text-[10px] tabular-nums text-muted-foreground">{fmtPct(maxDdPctCalc)}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase">Total trades</div>
          <div className="text-sm font-semibold tabular-nums text-foreground">{all.totalTrades}</div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase">Profitable trades</div>
          <div className="text-sm font-semibold tabular-nums text-foreground">
            {fmtPct(all.percentProfitable)} {all.winningTrades}/{all.totalTrades}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-muted-foreground uppercase">Profit factor</div>
          <div className="text-sm font-semibold tabular-nums text-foreground">{all.profitFactor.toFixed(3)}</div>
        </div>
      </div>

      <Accordion type="multiple" defaultValue={["performance", "returns", "benchmark", "risk-adjusted", "trades-analysis"]}>
      {/* ── Performance (Profit structure + Benchmarking) ── */}
      <AccordionItem value="performance">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Performance</AccordionTrigger>
        <AccordionContent>
        <div className="grid grid-cols-2 gap-6 px-3">
          {/* Profit structure bar chart */}
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-2">Profit structure</div>
            <ProfitStructureChart
              grossProfit={all.grossProfit}
              grossLoss={all.grossLoss}
              openPnl={0}
              commission={all.commissionPaid}
              netPnl={all.netPnl}
            />
          </div>
          {/* Benchmarking */}
          <div>
            <div className="text-[11px] font-medium text-muted-foreground mb-2">Benchmarking</div>
            <BenchmarkChart
              bhReturn={bhReturnUsdt}
              stratReturn={all.netPnl}
              bhPct={bhReturn}
              stratPct={all.netPnlPct}
              ic={ic}
            />
          </div>
        </div>
        </AccordionContent>
      </AccordionItem>

      {/* ── Returns ── */}
      <AccordionItem value="returns">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Returns</AccordionTrigger>
        <AccordionContent>
        <table className="w-full">
          <TableHeader />
          <tbody>
            <Row label="Initial capital" all={fmtUsdt(ic)} long="" short="" />
            <Row label="Open P&L" all={<V main={fmtSignUsdt(0)} sub={fmtSignPct(0)} color={cc(0)} />} long="" short="" />
            <Row label="Net P&L"
              all={<V main={fmtSignUsdt(all.netPnl)} sub={fmtSignPct(all.netPnlPct)} color={cc(all.netPnl)} />}
              long={<V main={fmtSignUsdt(lng.netPnl)} sub={fmtSignPct(lng.netPnlPct)} color={cc(lng.netPnl)} />}
              short={<V main={fmtSignUsdt(sht.netPnl)} sub={fmtSignPct(sht.netPnlPct)} color={cc(sht.netPnl)} />}
            />
            <Row label="Gross profit"
              all={<V main={fmtUsdt(all.grossProfit)} sub={fmtPct(all.grossProfitPct)} />}
              long={<V main={fmtUsdt(lng.grossProfit)} sub={fmtPct(lng.grossProfitPct)} />}
              short={<V main={fmtUsdt(sht.grossProfit)} sub={fmtPct(sht.grossProfitPct)} />}
            />
            <Row label="Gross loss"
              all={<V main={fmtUsdt(all.grossLoss)} sub={fmtPct(all.grossLossPct)} />}
              long={<V main={fmtUsdt(lng.grossLoss)} sub={fmtPct(lng.grossLossPct)} />}
              short={<V main={fmtUsdt(sht.grossLoss)} sub={fmtPct(sht.grossLossPct)} />}
            />
            <Row label="Profit factor"
              all={all.profitFactor.toFixed(3)}
              long={lng.profitFactor.toFixed(3)}
              short={sht.profitFactor.toFixed(3)}
            />
            <Row label="Commission paid"
              all={<span>{fmtInt(all.commissionPaid)} USDT</span>}
              long={<span>{fmtInt(lng.commissionPaid)} USDT</span>}
              short={<span>{fmtInt(sht.commissionPaid)} USDT</span>}
            />
            <Row label="Expected payoff"
              all={<span>{fmtUsdt(all.expectedPayoff)}</span>}
              long={<span>{fmtUsdt(lng.expectedPayoff)}</span>}
              short={<span>{fmtUsdt(sht.expectedPayoff)}</span>}
            />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>

      {/* ── Benchmark comparison ── */}
      <AccordionItem value="benchmark">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Benchmark comparison</AccordionTrigger>
        <AccordionContent>
        <table className="w-full">
          <TableHeader showLongShort={false} />
          <tbody>
            <Row showLongShort={false} label="Buy & hold return"
              all={<V main={fmtSignUsdt(bhReturnUsdt)} sub={fmtSignPct(bhReturn)} color={cc(bhReturnUsdt)} />}
            />
            <Row showLongShort={false} label="Buy & hold % gain"
              all={<span className={cc(bhReturn)}>{fmtSignPct(bhReturn)}</span>}
            />
            <Row showLongShort={false} label="Strategy outperformance"
              all={<span className={cc(strategyOutperformance)}>{fmtSignUsdt(strategyOutperformance)}</span>}
            />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>

      {/* ── Risk-adjusted performance ── */}
      <AccordionItem value="risk-adjusted">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Risk-adjusted performance</AccordionTrigger>
        <AccordionContent>
        <table className="w-full">
          <TableHeader showLongShort={false} />
          <tbody>
            <Row showLongShort={false} label="Sharpe ratio" all={r.sharpe_ratio.toFixed(3)} />
            <Row showLongShort={false} label="Sortino ratio" all={r.sortino_ratio.toFixed(3)} />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>

      {/* ── Trades analysis ── */}
      <AccordionItem value="trades-analysis">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Trades analysis</AccordionTrigger>
        <AccordionContent>
        {/* P&L Distribution + Win/loss ratio */}
        <div className="grid grid-cols-2 gap-6 px-3 mb-4">
          <PnlDistribution trades={trades} />
          <WinLossDonut
            total={all.totalTrades}
            wins={all.winningTrades}
            losses={all.losingTrades}
          />
        </div>

        {/* Details table */}
        <table className="w-full">
          <thead>
            <tr><td colSpan={4} className="py-2 px-3 text-[12px] font-semibold text-foreground bg-secondary/10">Details</td></tr>
          </thead>
          <TableHeader />
          <tbody>
            <Row label="Total trades" all={fmtInt(all.totalTrades)} long={fmtInt(lng.totalTrades)} short={fmtInt(sht.totalTrades)} />
            <Row label="Total open trades" all={fmtInt(0)} long={fmtInt(0)} short={fmtInt(0)} />
            <Row label="Winning trades" all={fmtInt(all.winningTrades)} long={fmtInt(lng.winningTrades)} short={fmtInt(sht.winningTrades)} />
            <Row label="Losing trades" all={fmtInt(all.losingTrades)} long={fmtInt(lng.losingTrades)} short={fmtInt(sht.losingTrades)} />
            <Row label="Percent profitable"
              all={fmtPct(all.percentProfitable)}
              long={fmtPct(lng.percentProfitable)}
              short={fmtPct(sht.percentProfitable)}
            />
            <Row label="Avg P&L"
              all={<V main={fmtUsdt(all.avgPnl)} sub={fmtPct(all.avgPnlPct)} />}
              long={<V main={fmtUsdt(lng.avgPnl)} sub={fmtPct(lng.avgPnlPct)} />}
              short={<V main={fmtUsdt(sht.avgPnl)} sub={fmtPct(sht.avgPnlPct)} />}
            />
            <Row label="Avg winning trade"
              all={<V main={fmtUsdt(all.avgWinTrade)} sub={fmtPct(all.avgWinPct)} />}
              long={<V main={fmtUsdt(lng.avgWinTrade)} sub={fmtPct(lng.avgWinPct)} />}
              short={<V main={fmtUsdt(sht.avgWinTrade)} sub={fmtPct(sht.avgWinPct)} />}
            />
            <Row label="Avg losing trade"
              all={<V main={fmtUsdt(all.avgLossTrade)} sub={fmtPct(all.avgLossPct)} />}
              long={<V main={fmtUsdt(lng.avgLossTrade)} sub={fmtPct(lng.avgLossPct)} />}
              short={<V main={fmtUsdt(sht.avgLossTrade)} sub={fmtPct(sht.avgLossPct)} />}
            />
            <Row label="Ratio avg win / avg loss"
              all={all.ratioAvgWinLoss.toFixed(3)}
              long={lng.ratioAvgWinLoss.toFixed(3)}
              short={sht.ratioAvgWinLoss.toFixed(3)}
            />
            <Row label="Largest winning trade"
              all={<span>{fmtUsdt(all.largestWin)}</span>}
              long={<span>{fmtUsdt(lng.largestWin)}</span>}
              short={<span>{fmtUsdt(sht.largestWin)}</span>}
            />
            <Row label="Largest winning trade percent"
              all={fmtPct(all.largestWinPct)}
              long={fmtPct(lng.largestWinPct)}
              short={fmtPct(sht.largestWinPct)}
            />
            <Row label="Largest winner as % of gross profit"
              all={fmtPct(all.largestWinAsGrossProfit)}
              long={fmtPct(lng.largestWinAsGrossProfit)}
              short={fmtPct(sht.largestWinAsGrossProfit)}
            />
            <Row label="Largest losing trade"
              all={<span>{fmtUsdt(all.largestLoss)}</span>}
              long={<span>{fmtUsdt(lng.largestLoss)}</span>}
              short={<span>{fmtUsdt(sht.largestLoss)}</span>}
            />
            <Row label="Largest losing trade percent"
              all={fmtPct(all.largestLossPct)}
              long={fmtPct(lng.largestLossPct)}
              short={fmtPct(sht.largestLossPct)}
            />
            <Row label="Largest loser as % of gross loss"
              all={fmtPct(all.largestLossAsGrossLoss)}
              long={fmtPct(lng.largestLossAsGrossLoss)}
              short={fmtPct(sht.largestLossAsGrossLoss)}
            />
            <Row label="Avg # bars in trades"
              all={fmtInt(all.avgBarsInTrades)}
              long={fmtInt(lng.avgBarsInTrades)}
              short={fmtInt(sht.avgBarsInTrades)}
            />
            <Row label="Avg # bars in winning trades"
              all={fmtInt(all.avgBarsInWinning)}
              long={fmtInt(lng.avgBarsInWinning)}
              short={fmtInt(sht.avgBarsInWinning)}
            />
            <Row label="Avg # bars in losing trades"
              all={fmtInt(all.avgBarsInLosing)}
              long={fmtInt(lng.avgBarsInLosing)}
              short={fmtInt(sht.avgBarsInLosing)}
            />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>

      {/* ── Capital efficiency ── */}
      <AccordionItem value="capital-efficiency">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Capital efficiency</AccordionTrigger>
        <AccordionContent>
        <table className="w-full">
          <thead><SectionHeader title="Capital usage" /></thead>
          <TableHeader />
          <tbody>
            <Row label="Annualized return (CAGR)"
              all={fmtPct(all.cagr)}
              long={fmtPct(lng.cagr)}
              short={fmtPct(sht.cagr)}
            />
            <Row label="Return on initial capital"
              all={fmtPct(all.returnOnInitialCapital)}
              long={fmtPct(lng.returnOnInitialCapital)}
              short={fmtPct(sht.returnOnInitialCapital)}
            />
            <Row label="Account size required" all={fmtUsdt(maxDdUsdt)} long="" short="" />
            <Row label="Return on account size required"
              all={fmtPct(all.returnOnAccountSize)}
              long={fmtPct(lng.returnOnAccountSize)}
              short={fmtPct(sht.returnOnAccountSize)}
            />
            <Row label="Net profit as % of largest loss"
              all={fmtPct(all.netProfitAsLargestLoss)}
              long={fmtPct(lng.netProfitAsLargestLoss)}
              short={fmtPct(sht.netProfitAsLargestLoss)}
            />
          </tbody>
        </table>
        <table className="w-full mt-2">
          <thead><SectionHeader title="Margin usage" /></thead>
          <TableHeader showLongShort={false} />
          <tbody>
            <Row showLongShort={false} label="Avg margin used" all="0 USDT" />
            <Row showLongShort={false} label="Max margin used" all="0 USDT" />
            <Row showLongShort={false} label="Margin efficiency" all="0 USDT" />
            <Row showLongShort={false} label="Margin calls" all="0" />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>

      {/* ── Run-ups and drawdowns ── */}
      <AccordionItem value="runups-drawdowns">
        <AccordionTrigger className="py-2 px-1 text-[13px] font-semibold text-foreground hover:no-underline hover:bg-muted/50">Run-ups and drawdowns</AccordionTrigger>
        <AccordionContent>
        <table className="w-full">
          <thead><SectionHeader title="Run-ups" /></thead>
          <TableHeader showLongShort={false} />
          <tbody>
            <Row showLongShort={false} label="Avg equity run-up duration (close-to-close)"
              all={`${fmtInt(runups.avgDuration)} days`}
            />
            <Row showLongShort={false} label="Avg equity run-up (close-to-close)"
              all={<V main={fmtUsdt(runups.avg)} sub={fmtPct(runups.avgPct)} />}
            />
            <Row showLongShort={false} label="Max equity run-up (close-to-close)"
              all={<V main={fmtUsdt(runups.max)} sub={fmtPct(runups.maxPct)} />}
            />
            <Row showLongShort={false} label="Max equity run-up (intrabar)"
              all={<V main={fmtUsdt(runups.max)} sub={fmtPct(runups.maxPct)} />}
            />
            <Row showLongShort={false} label="Max equity run-up as % of initial capital (intrabar)"
              all={fmtPct(ic > 0 ? runups.max / ic * 100 : 0)}
            />
          </tbody>
        </table>
        <table className="w-full mt-2">
          <thead><SectionHeader title="Drawdowns" /></thead>
          <TableHeader showLongShort={false} />
          <tbody>
            <Row showLongShort={false} label="Avg equity drawdown duration (close-to-close)"
              all={`${fmtInt(drawdowns.avgDuration)} days`}
            />
            <Row showLongShort={false} label="Avg equity drawdown (close-to-close)"
              all={<V main={fmtUsdt(drawdowns.avg)} sub={fmtPct(drawdowns.avgPct)} />}
            />
            <Row showLongShort={false} label="Max equity drawdown (close-to-close)"
              all={<V main={fmtUsdt(drawdowns.max)} sub={fmtPct(drawdowns.maxPct)} />}
            />
            <Row showLongShort={false} label="Max equity drawdown (intrabar)"
              all={<V main={fmtUsdt(maxDdUsdt)} sub={fmtPct(maxDdPctCalc)} />}
            />
            <Row showLongShort={false} label="Max equity drawdown as % of initial capital (intrabar)"
              all={fmtPct(ic > 0 ? maxDdUsdt / ic * 100 : 0)}
            />
            <Row showLongShort={false} label="Return of max equity drawdown"
              all={`${maxDdUsdt > 0 ? (all.netPnl / maxDdUsdt).toFixed(2) : '0.00'} USDT`}
            />
          </tbody>
        </table>
        </AccordionContent>
      </AccordionItem>
      </Accordion>
    </div>
  )
}

// ─── Run-ups / Drawdowns computation ────────────────────────────────────────

interface RunupDrawdownStats {
  avgDuration: number
  avg: number
  avgPct: number
  max: number
  maxPct: number
}

function computeRunupsDrawdowns(equity: number[], ic: number): { runups: RunupDrawdownStats; drawdowns: RunupDrawdownStats } {
  if (equity.length < 2) {
    const zero = { avgDuration: 0, avg: 0, avgPct: 0, max: 0, maxPct: 0 }
    return { runups: zero, drawdowns: zero }
  }

  // Run-ups: sequences of rising equity
  const runupAmts: number[] = []
  const runupDurs: number[] = []
  // Drawdowns: sequences of falling equity from peak
  const ddAmts: number[] = []
  const ddDurs: number[] = []

  let runStart = equity[0]
  let runDur = 0
  let peak = equity[0]
  let ddStart = equity[0]
  let ddDur = 0
  let maxDd = 0
  let maxRu = 0

  for (let i = 1; i < equity.length; i++) {
    const v = equity[i]
    // Run-up tracking
    if (v >= equity[i - 1]) {
      runDur++
    } else {
      if (runDur > 0) {
        const amt = equity[i - 1] - runStart
        if (amt > 0) { runupAmts.push(amt); runupDurs.push(runDur) }
        if (amt > maxRu) maxRu = amt
      }
      runStart = v
      runDur = 0
    }
    // Drawdown tracking
    if (v > peak) {
      if (ddDur > 0) {
        const amt = ddStart - equity[i - 1]
        if (amt > 0) { ddAmts.push(amt); ddDurs.push(ddDur) }
      }
      peak = v
      ddStart = v
      ddDur = 0
    } else {
      ddDur++
      const dd = peak - v
      if (dd > maxDd) maxDd = dd
    }
  }
  // Flush last sequences
  if (runDur > 0) {
    const amt = equity[equity.length - 1] - runStart
    if (amt > 0) { runupAmts.push(amt); runupDurs.push(runDur) }
    if (amt > maxRu) maxRu = amt
  }
  if (ddDur > 0) {
    const amt = ddStart - equity[equity.length - 1]
    if (amt > 0) { ddAmts.push(amt); ddDurs.push(ddDur) }
  }

  const avg = (arr: number[]) => arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0

  // Approximate days from bar count (assume hourly bars in equity curve)
  // Actually, without knowing the timeframe we'll assume the equity curve resolution
  // Just use bar counts as approximate day durations (since equity is sampled)

  return {
    runups: {
      avgDuration: Math.round(avg(runupDurs)),
      avg: avg(runupAmts),
      avgPct: ic > 0 ? avg(runupAmts) / ic * 100 : 0,
      max: maxRu,
      maxPct: ic > 0 ? maxRu / ic * 100 : 0,
    },
    drawdowns: {
      avgDuration: Math.round(avg(ddDurs)),
      avg: avg(ddAmts),
      avgPct: ic > 0 ? avg(ddAmts) / ic * 100 : 0,
      max: maxDd,
      maxPct: ic > 0 ? maxDd / ic * 100 : 0,
    },
  }
}

// ─── Charts ─────────────────────────────────────────────────────────────────

function ProfitStructureChart({ grossProfit, grossLoss, openPnl, commission, netPnl }: {
  grossProfit: number; grossLoss: number; openPnl: number; commission: number; netPnl: number
}) {
  const max = Math.max(grossProfit, grossLoss, Math.abs(netPnl), 1)
  const bar = (val: number, color: string, label: string) => (
    <div className="flex items-center gap-2 mb-1">
      <div className="w-24 text-[10px] text-muted-foreground text-right truncate">{label}</div>
      <div className="flex-1 h-4 bg-secondary/20 rounded-sm relative">
        <div
          className={`h-full rounded-sm ${color}`}
          style={{ width: `${Math.min(Math.abs(val) / max * 100, 100)}%` }}
        />
      </div>
      <div className="w-20 text-[10px] tabular-nums text-right text-muted-foreground">
        {fmtPrice(val)}
      </div>
    </div>
  )
  return (
    <div>
      {bar(grossProfit, 'bg-emerald-500', 'Total profit')}
      {bar(grossLoss, 'bg-red-500', 'Total loss')}
      {bar(Math.abs(openPnl), 'bg-amber-500', 'Open P&L')}
      {bar(commission, 'bg-blue-500', 'Commission')}
      {bar(netPnl, netPnl >= 0 ? 'bg-emerald-500' : 'bg-red-500', 'Total P&L')}
    </div>
  )
}

function BenchmarkChart({ bhReturn, stratReturn, bhPct, stratPct }: {
  bhReturn: number; stratReturn: number; bhPct: number; stratPct: number; ic: number
}) {
  const maxVal = Math.max(Math.abs(bhReturn), Math.abs(stratReturn), 1)
  const scale = (v: number) => (v / maxVal) * 45

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="w-20 text-[10px] text-muted-foreground text-right">Buy & Hold</div>
        <div className="flex-1 h-6 bg-secondary/10 rounded relative">
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-secondary/40" />
          <div
            className={`absolute top-0.5 bottom-0.5 rounded ${bhReturn >= 0 ? 'bg-amber-500/70' : 'bg-amber-500/70'}`}
            style={{
              left: bhReturn >= 0 ? '50%' : `${50 + scale(bhReturn)}%`,
              width: `${Math.abs(scale(bhReturn))}%`,
            }}
          />
        </div>
        <div className="w-24 text-[10px] tabular-nums text-right">
          <span className={cc(bhReturn)}>{fmtSignPct(bhPct)}</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <div className="w-20 text-[10px] text-muted-foreground text-right">Strategy</div>
        <div className="flex-1 h-6 bg-secondary/10 rounded relative">
          <div className="absolute top-0 bottom-0 left-1/2 w-px bg-secondary/40" />
          <div
            className={`absolute top-0.5 bottom-0.5 rounded ${stratReturn >= 0 ? 'bg-emerald-500/70' : 'bg-red-500/70'}`}
            style={{
              left: stratReturn >= 0 ? '50%' : `${50 + scale(stratReturn)}%`,
              width: `${Math.abs(scale(stratReturn))}%`,
            }}
          />
        </div>
        <div className="w-24 text-[10px] tabular-nums text-right">
          <span className={cc(stratReturn)}>{fmtSignPct(stratPct)}</span>
        </div>
      </div>
      <div className="flex gap-4 text-[10px] text-muted-foreground px-1">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-amber-500" /> P&L for buy & hold</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> P&L for strategy</span>
      </div>
    </div>
  )
}

function PnlDistribution({ trades }: { trades: TradeRecord[] }) {
  if (trades.length === 0) return <div className="text-muted-foreground text-xs">No trades</div>

  // Bucket P&L percentages
  const pcts = trades.map(t => t.pnl_pct)
  const minP = Math.floor(Math.min(...pcts) * 2) / 2
  const maxP = Math.ceil(Math.max(...pcts) * 2) / 2
  const step = 0.5
  const buckets: { label: string; count: number; isProfit: boolean }[] = []
  for (let b = minP; b <= maxP; b += step) {
    const lo = b
    const hi = b + step
    const count = trades.filter(t => t.pnl_pct >= lo && t.pnl_pct < hi).length
    buckets.push({ label: `${b.toFixed(1)}%`, count, isProfit: b >= 0 })
  }
  const maxCount = Math.max(...buckets.map(b => b.count), 1)

  const winPcts = trades.filter(t => t.pnl > 0).map(t => t.pnl_pct)
  const lossPcts = trades.filter(t => t.pnl <= 0).map(t => t.pnl_pct)
  const avgProfit = winPcts.length > 0 ? winPcts.reduce((a, b) => a + b, 0) / winPcts.length : 0
  const avgLoss = lossPcts.length > 0 ? lossPcts.reduce((a, b) => a + b, 0) / lossPcts.length : 0

  return (
    <div>
      <div className="text-[11px] font-medium text-muted-foreground mb-2">P&L Distribution</div>
      <div className="flex items-end gap-px h-20">
        {buckets.map((b, i) => (
          <div key={i} className="flex-1 flex flex-col items-center justify-end h-full">
            <div
              className={`w-full rounded-t-sm ${b.isProfit ? 'bg-emerald-500' : 'bg-red-500'}`}
              style={{ height: `${b.count / maxCount * 100}%`, minHeight: b.count > 0 ? 2 : 0 }}
            />
          </div>
        ))}
      </div>
      <div className="flex justify-between text-[9px] text-muted-foreground mt-1">
        <span>{buckets[0]?.label}</span>
        <span>{buckets[buckets.length - 1]?.label}</span>
      </div>
      <div className="flex gap-3 text-[10px] text-muted-foreground mt-1">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-red-500" /> Loss</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-full bg-emerald-500" /> Profit</span>
        <span className="ml-auto">Average loss <strong className="text-tv-red">{avgLoss.toFixed(2)}%</strong></span>
        <span>Average profit <strong className="text-tv-green">{avgProfit.toFixed(2)}%</strong></span>
      </div>
    </div>
  )
}

function WinLossDonut({ total, wins, losses }: { total: number; wins: number; losses: number }) {
  const winPct = total > 0 ? wins / total : 0
  const lossPct = total > 0 ? losses / total : 0
  const r = 40
  const circumference = 2 * Math.PI * r
  const winArc = winPct * circumference
  const lossArc = lossPct * circumference

  return (
    <div>
      <div className="text-[11px] font-medium text-muted-foreground mb-2">Win/loss ratio</div>
      <div className="flex items-center gap-4">
        <div className="relative" style={{ width: 100, height: 100 }}>
          <svg viewBox="0 0 100 100" width="100" height="100">
            {/* Loss arc (background) */}
            <circle cx="50" cy="50" r={r} fill="none" stroke="#ef5350" strokeWidth="8" strokeDasharray={`${lossArc} ${circumference}`} strokeDashoffset={-winArc} transform="rotate(-90 50 50)" />
            {/* Win arc */}
            <circle cx="50" cy="50" r={r} fill="none" stroke="#26a69a" strokeWidth="8" strokeDasharray={`${winArc} ${circumference}`} transform="rotate(-90 50 50)" />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <div className="text-lg font-bold text-foreground tabular-nums">{total}</div>
            <div className="text-[9px] text-muted-foreground">Total trades</div>
          </div>
        </div>
        <div className="space-y-1 text-[11px]">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
            <span className="text-muted-foreground">Wins</span>
            <span className="ml-2 tabular-nums text-foreground">{wins} trades</span>
            <span className="ml-1 tabular-nums text-muted-foreground">{(winPct * 100).toFixed(2)}%</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
            <span className="text-muted-foreground">Losses</span>
            <span className="ml-2 tabular-nums text-foreground">{losses} trades</span>
            <span className="ml-1 tabular-nums text-muted-foreground">{(lossPct * 100).toFixed(2)}%</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-500" />
            <span className="text-muted-foreground">Break even</span>
            <span className="ml-2 tabular-nums text-foreground">0 trades</span>
            <span className="ml-1 tabular-nums text-muted-foreground">0.00%</span>
          </div>
        </div>
      </div>
    </div>
  )
}

// ─── Tab: Trades List (DataTable) ─────────────────────────────────────────

/** Extended trade row with pre-computed cumulative P&L for column rendering. */
interface TradeRow extends TradeRecord {
  _index: number
  _cumPnl: number
  _cumPnlPct: number
}

function TradesListTab({ trades, initialCapital, timezone }: { trades: TradeRecord[]; initialCapital: number; timezone: string }) {
  const rows: TradeRow[] = useMemo(() => {
    let cum = 0
    return trades.map((t, i) => {
      cum += t.pnl
      return { ...t, _index: i + 1, _cumPnl: cum, _cumPnlPct: (cum / initialCapital) * 100 }
    })
  }, [trades, initialCapital])

  const columns: ColumnDef<TradeRow, unknown>[] = useMemo(
    () => [
      {
        accessorKey: '_index',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Trade # <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => {
          const isLong = row.original.side === 'buy'
          return (
            <div className="flex items-center gap-1.5">
              <span className="text-muted-foreground tabular-nums">{row.original._index}</span>
              <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded-sm', isLong ? 'bg-emerald-500/15 text-tv-green' : 'bg-red-500/15 text-tv-red')}>
                {isLong ? 'Long' : 'Short'}
              </span>
            </div>
          )
        },
      },
      {
        accessorKey: 'entry_time',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Entry time <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => (
          <span className="text-muted-foreground whitespace-nowrap text-[11px]">{fmtDateTz(row.original.entry_time ?? row.original.timestamp, timezone)}</span>
        ),
      },
      {
        accessorKey: 'price',
        header: () => <span className="flex justify-end text-[11px]">Entry price</span>,
        cell: ({ row }) => (
          <div className="text-right tabular-nums whitespace-nowrap text-[11px]">{fmtPrice(row.original.price)} USDT</div>
        ),
      },
      {
        accessorKey: 'exit_time',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Exit time <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => (
          <span className="whitespace-nowrap text-muted-foreground text-[11px]">{fmtDateTz(row.original.exit_time, timezone)}</span>
        ),
      },
      {
        accessorKey: 'exit_price',
        header: () => <span className="flex justify-end text-[11px]">Exit price</span>,
        cell: ({ row }) => (
          <div className="text-right tabular-nums whitespace-nowrap text-[11px]">{fmtPrice(row.original.exit_price)} USDT</div>
        ),
      },
      {
        accessorKey: 'amount',
        header: () => <div className="text-right text-[11px]">Position size</div>,
        cell: ({ row }) => (
          <div className="text-right tabular-nums text-[11px]">
            <div>{row.original.amount.toFixed(2)}</div>
            <div className="text-[10px] text-muted-foreground">{fmtPrice(row.original.amount * row.original.price)} USDT</div>
          </div>
        ),
      },
      {
        accessorKey: 'pnl',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium ml-auto flex" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Net P&L <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => (
          <div className="text-right text-[11px]">
            <div className={cn('tabular-nums font-medium', cc(row.original.pnl))}>{fmtSignUsdt(row.original.pnl)}</div>
            <div className={cn('text-[10px] tabular-nums', cc(row.original.pnl_pct))}>{fmtSignPct(row.original.pnl_pct)}</div>
          </div>
        ),
      },
      {
        id: 'mfe',
        header: () => <span className="flex justify-end text-[11px]">Favorable excursion</span>,
        cell: ({ row }) => (
          <div className="text-right text-[11px]">
            <div className="tabular-nums text-tv-green">{row.original.mfe != null ? fmtSignUsdt(row.original.mfe) : '\u2014'}</div>
            <div className="text-[10px] tabular-nums text-tv-green">{row.original.mfe_pct != null ? fmtSignPct(row.original.mfe_pct) : ''}</div>
          </div>
        ),
      },
      {
        id: 'mae',
        header: () => <span className="flex justify-end text-[11px]">Adverse excursion</span>,
        cell: ({ row }) => (
          <div className="text-right text-[11px]">
            <div className="tabular-nums text-tv-red">{row.original.mae != null ? fmtSignUsdt(row.original.mae) : '\u2014'}</div>
            <div className="text-[10px] tabular-nums text-tv-red">{row.original.mae_pct != null ? fmtSignPct(row.original.mae_pct) : ''}</div>
          </div>
        ),
      },
      {
        accessorKey: '_cumPnl',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium ml-auto flex" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Cumulative P&L <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => (
          <div className="text-right text-[11px]">
            <div className={cn('tabular-nums font-medium', cc(row.original._cumPnl))}>{fmtSignUsdt(row.original._cumPnl)}</div>
            <div className={cn('text-[10px] tabular-nums', cc(row.original._cumPnlPct))}>{fmtSignPct(row.original._cumPnlPct)}</div>
          </div>
        ),
      },
      {
        accessorKey: 'bars_held',
        header: ({ column }) => (
          <Button variant="ghost" size="sm" className="h-auto px-1 py-0 text-[11px] font-medium ml-auto flex" onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}>
            Bars <ArrowUpDown className="ml-1 h-3 w-3" />
          </Button>
        ),
        cell: ({ row }) => (
          <div className="text-right tabular-nums text-[11px]">{row.original.bars_held ?? '\u2014'}</div>
        ),
      },
    ],
    [timezone],
  )

  return <DataTable columns={columns} data={rows} pageSize={15} />
}

// ─── Main StrategyTester component ──────────────────────────────────────────

interface Props {
  result: BacktestResult
}

export default function StrategyTester({ result }: Props) {
  const { timezone } = useTimezone()

  return (
    <Tabs defaultValue="report" className="flex flex-col h-full">
      <div className="flex items-center border-b border-border shrink-0">
        <span className="text-[11px] font-semibold text-muted-foreground px-3 py-2 border-r border-border mr-1">
          Strategy Tester
        </span>
        <TabsList className="bg-transparent h-auto p-0">
          <TabsTrigger
            value="report"
            className="px-4 py-2 text-xs font-medium rounded-none border-b-2 border-transparent h-auto data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:border-primary data-[state=active]:shadow-none text-muted-foreground hover:text-foreground"
          >
            Strategy Report
          </TabsTrigger>
          <TabsTrigger
            value="trades"
            className="px-4 py-2 text-xs font-medium rounded-none border-b-2 border-transparent h-auto data-[state=active]:bg-transparent data-[state=active]:text-primary data-[state=active]:border-primary data-[state=active]:shadow-none text-muted-foreground hover:text-foreground"
          >
            List of Trades
          </TabsTrigger>
        </TabsList>
        {/* Summary info */}
        <div className="ml-auto flex items-center gap-4 pr-3 text-xs text-muted-foreground">
          <span>
            {result.strategy} &middot; {result.exchange}
          </span>
          <span className={cn('font-medium', result.total_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red')}>
            {result.total_return_pct >= 0 ? '+' : ''}{result.total_return_pct.toFixed(2)}%
          </span>
        </div>
      </div>

      <TabsContent value="report" className="flex-1 overflow-y-auto p-4 mt-0">
        <StrategyReportTab r={result} />
      </TabsContent>
      <TabsContent value="trades" className="flex-1 overflow-y-auto p-4 mt-0">
        <TradesListTab trades={result.trades} initialCapital={result.initial_capital} timezone={timezone} />
      </TabsContent>
    </Tabs>
  )
}


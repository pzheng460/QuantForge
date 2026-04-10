import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  ChartLegend,
  ChartLegendContent,
  type ChartConfig,
} from '@/components/ui/chart'
import type { EquityPoint } from '../../types'
import { useTimezone, fmtDateShortTz } from '../../hooks/useTimezone'

interface Props {
  data: EquityPoint[]
  initialCapital?: number
}

function fmt(v: number) {
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000) return `$${(v / 1_000).toFixed(1)}K`
  return `$${v.toFixed(0)}`
}

const chartConfig = {
  strategy: {
    label: 'Strategy',
    color: '#6366f1',
  },
  bh: {
    label: 'Buy & Hold',
    color: '#f59e0b',
  },
} satisfies ChartConfig

export default function EquityChart({ data }: Props) {
  const { timezone } = useTimezone()
  const fmtDate = (iso: string) => fmtDateShortTz(iso, timezone)
  if (!data || data.length === 0) return null

  return (
    <ChartContainer config={chartConfig} className="min-h-[320px] w-full">
      <LineChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          tickFormatter={fmtDate}
          tick={{ fontSize: 11 }}
          minTickGap={60}
        />
        <YAxis tickFormatter={fmt} tick={{ fontSize: 11 }} width={70} />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(value, payload) => {
                if (payload?.[0]?.payload?.t) {
                  return fmtDate(payload[0].payload.t)
                }
                return fmtDate(value as string)
              }}
              formatter={(value, name) => {
                const label =
                  chartConfig[name as keyof typeof chartConfig]?.label ?? name
                return (
                  <>
                    <div
                      className="h-2.5 w-2.5 shrink-0 rounded-[2px] bg-[--color-bg]"
                      style={
                        {
                          '--color-bg': `var(--color-${name})`,
                        } as React.CSSProperties
                      }
                    />
                    {label}
                    <div className="ml-auto flex items-baseline gap-0.5 font-mono font-medium tabular-nums text-foreground">
                      {fmt(value as number)}
                    </div>
                  </>
                )
              }}
            />
          }
        />
        <ChartLegend content={<ChartLegendContent />} />
        <Line
          type="monotone"
          dataKey="strategy"
          stroke="var(--color-strategy)"
          dot={false}
          strokeWidth={2}
        />
        <Line
          type="monotone"
          dataKey="bh"
          stroke="var(--color-bh)"
          dot={false}
          strokeWidth={1.5}
          strokeDasharray="5 3"
        />
      </LineChart>
    </ChartContainer>
  )
}

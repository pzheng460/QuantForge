import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart'
import type { DrawdownPoint } from '../../types'
import { useTimezone, fmtDateShortTz } from '../../hooks/useTimezone'

interface Props {
  data: DrawdownPoint[]
}

const chartConfig = {
  dd: {
    label: 'Drawdown',
    color: '#ef4444',
  },
} satisfies ChartConfig

export default function DrawdownChart({ data }: Props) {
  const { timezone } = useTimezone()
  const fmtDate = (iso: string) => fmtDateShortTz(iso, timezone)
  if (!data || data.length === 0) return null

  return (
    <ChartContainer config={chartConfig} className="min-h-[200px] w-full">
      <AreaChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="fillDrawdown" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="var(--color-dd)" stopOpacity={0.3} />
            <stop offset="95%" stopColor="var(--color-dd)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          dataKey="t"
          tickFormatter={fmtDate}
          tick={{ fontSize: 11 }}
          minTickGap={60}
        />
        <YAxis
          tickFormatter={(v) => `${v.toFixed(1)}%`}
          tick={{ fontSize: 11 }}
          width={55}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              labelFormatter={(value, payload) => {
                const t = payload?.[0]?.payload?.t
                return t ? fmtDate(t) : fmtDate(value as string)
              }}
              formatter={(value) => (
                <>
                  <div
                    className="h-2.5 w-2.5 shrink-0 rounded-[2px] bg-[--color-bg]"
                    style={
                      {
                        '--color-bg': 'var(--color-dd)',
                      } as React.CSSProperties
                    }
                  />
                  Drawdown
                  <div className="ml-auto flex items-baseline gap-0.5 font-mono font-medium tabular-nums text-foreground">
                    {(value as number).toFixed(2)}%
                  </div>
                </>
              )}
            />
          }
        />
        <Area
          type="monotone"
          dataKey="dd"
          stroke="var(--color-dd)"
          fill="url(#fillDrawdown)"
          strokeWidth={1.5}
          dot={false}
        />
      </AreaChart>
    </ChartContainer>
  )
}

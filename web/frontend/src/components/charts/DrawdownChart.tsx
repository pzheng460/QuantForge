import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import type { DrawdownPoint } from '../../types'

interface Props {
  data: DrawdownPoint[]
}

function fmtDate(iso: string) {
  return iso.slice(0, 10)
}

export default function DrawdownChart({ data }: Props) {
  if (!data || data.length === 0) return null

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
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
        <Tooltip
          formatter={(v: number) => [`${v.toFixed(2)}%`, 'Drawdown']}
          labelFormatter={fmtDate}
        />
        <Area
          type="monotone"
          dataKey="dd"
          stroke="#ef4444"
          fill="url(#ddGrad)"
          strokeWidth={1.5}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

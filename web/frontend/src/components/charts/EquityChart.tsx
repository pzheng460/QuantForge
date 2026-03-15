import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
} from 'recharts'
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

export default function EquityChart({ data }: Props) {
  const { timezone } = useTimezone()
  const fmtDate = (iso: string) => fmtDateShortTz(iso, timezone)
  if (!data || data.length === 0) return null

  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
        <XAxis
          dataKey="t"
          tickFormatter={fmtDate}
          tick={{ fontSize: 11 }}
          minTickGap={60}
        />
        <YAxis tickFormatter={fmt} tick={{ fontSize: 11 }} width={70} />
        <Tooltip
          formatter={(v: number, name: string) => [fmt(v), name === 'strategy' ? 'Strategy' : 'B&H']}
          labelFormatter={fmtDate}
        />
        <Legend formatter={(v) => (v === 'strategy' ? 'Strategy' : 'Buy & Hold')} />
        <Line
          type="monotone"
          dataKey="strategy"
          stroke="#6366f1"
          dot={false}
          strokeWidth={2}
        />
        <Line
          type="monotone"
          dataKey="bh"
          stroke="#f59e0b"
          dot={false}
          strokeWidth={1.5}
          strokeDasharray="5 3"
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

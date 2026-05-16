import type { MonthlyReturn } from '../../types'

interface Props {
  data: MonthlyReturn[]
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function color(ret: number): string {
  if (ret > 15) return 'bg-green-700 text-white'
  if (ret > 8) return 'bg-green-500 text-white'
  if (ret > 3) return 'bg-green-300 text-foreground'
  if (ret > 0) return 'bg-green-100 text-foreground'
  if (ret > -3) return 'bg-red-100 text-foreground'
  if (ret > -8) return 'bg-red-300 text-foreground'
  if (ret > -15) return 'bg-red-500 text-white'
  return 'bg-red-700 text-white'
}

export default function MonthlyReturnsHeatmap({ data }: Props) {
  if (!data || data.length === 0) return null

  // Build lookup: year -> month -> return
  const lookup: Record<number, Record<number, number>> = {}
  for (const d of data) {
    if (!lookup[d.year]) lookup[d.year] = {}
    lookup[d.year][d.month] = d.return
  }

  const years = Object.keys(lookup).map(Number).sort()

  return (
    <div className="overflow-x-auto">
      <table className="text-xs w-full border-collapse">
        <thead>
          <tr>
            <th className="py-1 px-2 text-left text-muted-foreground font-medium w-14">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="py-1 px-1 text-center text-muted-foreground font-medium">
                {m}
              </th>
            ))}
            <th className="py-1 px-2 text-center text-muted-foreground font-medium w-14">Total</th>
          </tr>
        </thead>
        <tbody>
          {years.map((year) => {
            const monthly = lookup[year]
            let yearTotal = 1
            for (let m = 1; m <= 12; m++) {
              if (monthly[m] !== undefined) yearTotal *= 1 + monthly[m] / 100
            }
            const yearRet = (yearTotal - 1) * 100

            return (
              <tr key={year}>
                <td className="py-1 px-2 font-medium text-foreground">{year}</td>
                {Array.from({ length: 12 }, (_, i) => i + 1).map((m) => {
                  const v = monthly[m]
                  return (
                    <td key={m} className="py-0.5 px-0.5">
                      {v !== undefined ? (
                        <div
                          className={`text-center py-1 font-medium rounded ${color(v)}`}
                          title={`${v.toFixed(2)}%`}
                        >
                          {v > 0 ? '+' : ''}
                          {v.toFixed(1)}
                        </div>
                      ) : (
                        <div className="rounded text-center py-1 text-muted-foreground/50">
                          &mdash;
                        </div>
                      )}
                    </td>
                  )
                })}
                <td className="py-0.5 px-0.5">
                  <div
                    className={`rounded text-center py-1 font-bold ${color(yearRet)}`}
                  >
                    {yearRet > 0 ? '+' : ''}
                    {yearRet.toFixed(1)}
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

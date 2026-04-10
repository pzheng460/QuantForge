import type { HeatmapResult, HeatmapMesa } from '../../types'
import { cn } from '@/lib/utils'

interface Props {
  data: HeatmapResult
}

/** Map a Sharpe value to color classes (keep green/red heatmap intensities). */
function sharpeColor(v: number | null): string {
  if (v === null || v === undefined) return 'bg-muted text-muted-foreground/50'
  if (v >= 2.0) return 'bg-green-700 text-white'
  if (v >= 1.5) return 'bg-green-500 text-white'
  if (v >= 1.0) return 'bg-green-300 text-foreground'
  if (v >= 0.5) return 'bg-green-100 text-foreground'
  if (v >= 0.0) return 'bg-muted text-muted-foreground'
  if (v >= -0.5) return 'bg-red-100 text-foreground'
  return 'bg-red-300 text-foreground'
}

function fmt(v: number, digits = 1) {
  return v.toFixed(digits)
}

export default function HeatmapChart({ data }: Props) {
  const { x_values, y_values, sharpe_grid, x_label, y_label, mesas } = data

  // sharpe_grid is indexed [yi][xi]; display top (high y) to bottom (low y)
  const reversedY = [...y_values].reverse()
  const reversedGrid = [...sharpe_grid].reverse()

  return (
    <div className="space-y-6">
      {/* Heatmap grid */}
      <div className="overflow-x-auto">
        <div className="inline-block">
          <div className="flex items-center mb-1">
            <div className="w-16 shrink-0" />
            <div className="text-xs text-muted-foreground font-medium text-center flex-1">
              {x_label}
            </div>
          </div>
          <div className="flex">
            {/* Y-axis labels */}
            <div className="flex flex-col justify-between w-16 shrink-0 text-right pr-2">
              {reversedY.map((yv) => (
                <div key={yv} className="text-xs text-muted-foreground leading-none" style={{ height: 22 }}>
                  {fmt(yv)}
                </div>
              ))}
              <div className="text-xs text-muted-foreground text-center -rotate-90 mt-2 origin-center whitespace-nowrap">
                {y_label}
              </div>
            </div>

            {/* Grid cells */}
            <div className="flex flex-col gap-0.5">
              {reversedGrid.map((row, ri) => (
                <div key={ri} className="flex gap-0.5">
                  {row.map((v, ci) => (
                    <div
                      key={ci}
                      className={cn(
                        'text-center text-[10px] font-medium rounded-sm select-none cursor-default',
                        sharpeColor(v),
                      )}
                      style={{ width: 40, height: 22, lineHeight: '22px' }}
                      title={`${x_label}=${fmt(x_values[ci])}, ${y_label}=${fmt(reversedY[ri])}, Sharpe=${v !== null ? fmt(v, 2) : 'N/A'}`}
                    >
                      {v !== null ? fmt(v, 1) : '\u2014'}
                    </div>
                  ))}
                </div>
              ))}
              {/* X-axis ticks */}
              <div className="flex gap-0.5 mt-1">
                {x_values.map((xv, i) => (
                  <div
                    key={i}
                    className="text-[10px] text-muted-foreground text-center"
                    style={{ width: 40 }}
                  >
                    {fmt(xv)}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Color legend */}
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        <span>Sharpe:</span>
        {[
          { label: '\u22652.0', cls: 'bg-green-700' },
          { label: '\u22651.5', cls: 'bg-green-500' },
          { label: '\u22651.0', cls: 'bg-green-300' },
          { label: '\u22650.5', cls: 'bg-green-100 border border-border' },
          { label: '\u22650', cls: 'bg-muted border border-border' },
          { label: '<0', cls: 'bg-red-100' },
        ].map(({ label, cls }) => (
          <div key={label} className="flex items-center gap-1">
            <div className={cn('w-4 h-4 rounded-sm', cls)} />
            <span>{label}</span>
          </div>
        ))}
      </div>

      {/* Mesa regions table */}
      {mesas.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            Stable Regions (MESA)
          </h4>
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr className="border-b border-border">
                  {['#', 'Center X', 'Center Y', 'Avg Sharpe', 'Avg Return', 'Stability', 'Area', 'Frequency'].map(
                    (h) => (
                      <th key={h} className="py-2 px-3 text-left text-muted-foreground font-medium">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {mesas.map((m: HeatmapMesa, i) => (
                  <tr key={i} className="border-b border-border hover:bg-muted/50">
                    <td className="py-1.5 px-3 text-muted-foreground">{m.index + 1}</td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.center_x, 2)}</td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.center_y, 1)}</td>
                    <td
                      className={cn(
                        'py-1.5 px-3 tabular-nums font-medium',
                        m.avg_sharpe >= 1 ? 'text-tv-green' : 'text-foreground',
                      )}
                    >
                      {fmt(m.avg_sharpe, 2)}
                    </td>
                    <td
                      className={cn(
                        'py-1.5 px-3 tabular-nums',
                        m.avg_return_pct >= 0 ? 'text-tv-green' : 'text-tv-red',
                      )}
                    >
                      {m.avg_return_pct >= 0 ? '+' : ''}
                      {fmt(m.avg_return_pct, 1)}%
                    </td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.stability, 3)}</td>
                    <td className="py-1.5 px-3 tabular-nums">{m.area}</td>
                    <td className="py-1.5 px-3 text-muted-foreground">{m.frequency_label}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

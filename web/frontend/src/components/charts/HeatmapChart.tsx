import type { HeatmapResult, HeatmapMesa } from '../../types'
import clsx from 'clsx'

interface Props {
  data: HeatmapResult
}

/** Map a Sharpe value to a Tailwind background + text color */
function sharpeColor(v: number | null): string {
  if (v === null || v === undefined) return 'bg-gray-100 text-gray-300'
  if (v >= 2.0) return 'bg-green-700 text-white'
  if (v >= 1.5) return 'bg-green-500 text-white'
  if (v >= 1.0) return 'bg-green-300 text-gray-900'
  if (v >= 0.5) return 'bg-green-100 text-gray-700'
  if (v >= 0.0) return 'bg-gray-50 text-gray-500'
  if (v >= -0.5) return 'bg-red-100 text-gray-600'
  return 'bg-red-300 text-gray-900'
}

function fmt(v: number, digits = 1) {
  return v.toFixed(digits)
}

export default function HeatmapChart({ data }: Props) {
  const { x_values, y_values, sharpe_grid, x_label, y_label, mesas } = data

  // sharpe_grid is indexed [yi][xi] (rows = y, cols = x)
  // Display: y-axis rows from top (high y) to bottom (low y)
  const reversedY = [...y_values].reverse()
  const reversedGrid = [...sharpe_grid].reverse()

  return (
    <div className="space-y-6">
      {/* Heatmap grid */}
      <div className="overflow-x-auto">
        <div className="inline-block min-w-full">
          {/* X-axis header */}
          <div className="flex items-center mb-1">
            <div className="w-16 shrink-0" />
            <div className="text-xs text-gray-400 font-medium text-center flex-1">
              {x_label}
            </div>
          </div>
          <div className="flex">
            {/* Y-axis labels */}
            <div className="flex flex-col justify-between w-16 shrink-0 text-right pr-2">
              {reversedY.map((yv) => (
                <div key={yv} className="text-xs text-gray-400 leading-none" style={{ height: 22 }}>
                  {fmt(yv)}
                </div>
              ))}
              <div className="text-xs text-gray-400 text-center -rotate-90 mt-2 origin-center whitespace-nowrap">
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
                      className={clsx(
                        'text-center text-[10px] font-medium rounded-sm select-none cursor-default',
                        sharpeColor(v),
                      )}
                      style={{ width: 40, height: 22, lineHeight: '22px' }}
                      title={`${x_label}=${fmt(x_values[ci])}, ${y_label}=${fmt(reversedY[ri])}, Sharpe=${v !== null ? fmt(v, 2) : 'N/A'}`}
                    >
                      {v !== null ? fmt(v, 1) : '—'}
                    </div>
                  ))}
                </div>
              ))}
              {/* X-axis ticks */}
              <div className="flex gap-0.5 mt-1">
                {x_values.map((xv, i) => (
                  <div
                    key={i}
                    className="text-[10px] text-gray-400 text-center"
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

      {/* Color scale legend */}
      <div className="flex items-center gap-1 text-xs text-gray-500">
        <span>Sharpe:</span>
        {[
          { label: '≥2.0', cls: 'bg-green-700' },
          { label: '≥1.5', cls: 'bg-green-500' },
          { label: '≥1.0', cls: 'bg-green-300' },
          { label: '≥0.5', cls: 'bg-green-100 border border-gray-200' },
          { label: '≥0', cls: 'bg-gray-50 border border-gray-200' },
          { label: '<0', cls: 'bg-red-100' },
        ].map(({ label, cls }) => (
          <div key={label} className="flex items-center gap-1">
            <div className={clsx('w-4 h-4 rounded-sm', cls)} />
            <span>{label}</span>
          </div>
        ))}
      </div>

      {/* Mesa regions table */}
      {mesas.length > 0 && (
        <div>
          <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">
            Stable Regions (MESA)
          </h4>
          <div className="overflow-x-auto">
            <table className="text-xs w-full">
              <thead>
                <tr className="border-b border-gray-100">
                  {['#', 'Center X', 'Center Y', 'Avg Sharpe', 'Avg Return', 'Stability', 'Area', 'Frequency'].map((h) => (
                    <th key={h} className="py-2 px-3 text-left text-gray-400 font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {mesas.map((m: HeatmapMesa, i) => (
                  <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1.5 px-3 text-gray-400">{m.index + 1}</td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.center_x, 2)}</td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.center_y, 1)}</td>
                    <td className={clsx('py-1.5 px-3 tabular-nums font-medium', m.avg_sharpe >= 1 ? 'text-green-600' : 'text-gray-700')}>
                      {fmt(m.avg_sharpe, 2)}
                    </td>
                    <td className={clsx('py-1.5 px-3 tabular-nums', m.avg_return_pct >= 0 ? 'text-green-600' : 'text-red-500')}>
                      {m.avg_return_pct >= 0 ? '+' : ''}{fmt(m.avg_return_pct, 1)}%
                    </td>
                    <td className="py-1.5 px-3 tabular-nums">{fmt(m.stability, 3)}</td>
                    <td className="py-1.5 px-3 tabular-nums">{m.area}</td>
                    <td className="py-1.5 px-3 text-gray-500">{m.frequency_label}</td>
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

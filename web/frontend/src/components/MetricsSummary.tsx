import { useMemo } from 'react'
import clsx from 'clsx'
import type { AgentEvent, AgentMetric } from '../types'

interface MetricsSummaryProps {
  events: AgentEvent[]
  metrics: AgentMetric[]
  className?: string
}

interface ParsedMetric {
  name: string
  value: number
  iteration: number
  timestamp: string
  higher_is_better?: boolean
}

function parseMetricsFromEvents(events: AgentEvent[], metrics: AgentMetric[]): ParsedMetric[] {
  const parsedMetrics: ParsedMetric[] = []
  let currentIteration = 1

  for (const event of events) {
    if (event.type === 'tool_result' && event.content) {
      // Check for iteration markers
      if (event.content.includes('Iteration') || event.content.includes('iteration')) {
        const iterMatch = event.content.match(/[Ii]teration[:\s]+(\d+)/);
        if (iterMatch) {
          currentIteration = parseInt(iterMatch[1])
        }
      }

      // Parse metrics using regex patterns
      for (const metric of metrics) {
        try {
          const regex = new RegExp(metric.pattern, 'g')
          let match
          while ((match = regex.exec(event.content)) !== null) {
            const value = parseFloat(match[1])
            if (!isNaN(value)) {
              parsedMetrics.push({
                name: metric.name,
                value,
                iteration: currentIteration,
                timestamp: event.timestamp,
                higher_is_better: metric.higher_is_better
              })
            }
          }
        } catch (error) {
          // Invalid regex, skip
          console.warn(`Invalid regex pattern for metric ${metric.name}:`, metric.pattern)
        }
      }
    }
  }

  return parsedMetrics
}

function MetricCard({ name, values, higherIsBetter }: {
  name: string
  values: ParsedMetric[]
  higherIsBetter?: boolean
}) {
  if (values.length === 0) return null

  const latest = values[values.length - 1]
  const previous = values.length > 1 ? values[values.length - 2] : null

  const improved = previous ? (
    higherIsBetter === true ? latest.value > previous.value :
    higherIsBetter === false ? latest.value < previous.value :
    false
  ) : false

  const regressed = previous ? (
    higherIsBetter === true ? latest.value < previous.value :
    higherIsBetter === false ? latest.value > previous.value :
    false
  ) : false

  return (
    <div className="border border-tv-border rounded-lg p-3 bg-tv-panel">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-tv-text">{name}</span>
        <div className="flex items-center gap-1">
          {values.map((value, i) => (
            <div
              key={i}
              className={clsx(
                'w-2 h-2 rounded-full',
                i === values.length - 1
                  ? (improved ? 'bg-green-400' : regressed ? 'bg-red-400' : 'bg-tv-blue')
                  : 'bg-tv-border'
              )}
              title={`Iteration ${value.iteration}: ${value.value}`}
            />
          ))}
        </div>
      </div>

      <div className="flex items-center justify-between">
        <span className={clsx(
          'text-lg font-semibold tabular-nums',
          improved ? 'text-green-400' :
          regressed ? 'text-red-400' : 'text-tv-text'
        )}>
          {latest.value.toFixed(2)}
        </span>

        {previous && (
          <div className="text-xs">
            <span className={clsx(
              'font-medium',
              improved ? 'text-green-400' :
              regressed ? 'text-red-400' : 'text-tv-muted'
            )}>
              {improved ? '↗' : regressed ? '↘' : '→'}
              {Math.abs(((latest.value - previous.value) / previous.value) * 100).toFixed(1)}%
            </span>
          </div>
        )}
      </div>

      {values.length > 1 && (
        <div className="mt-2 text-xs text-tv-muted">
          Iteration {values.map(v => v.iteration).join(' → ')}
        </div>
      )}
    </div>
  )
}

export default function MetricsSummary({ events, metrics, className }: MetricsSummaryProps) {
  const parsedMetrics = useMemo(() => parseMetricsFromEvents(events, metrics), [events, metrics])

  // Group metrics by name
  const metricsByName = useMemo(() => {
    const groups: Record<string, ParsedMetric[]> = {}
    for (const metric of parsedMetrics) {
      if (!groups[metric.name]) groups[metric.name] = []
      groups[metric.name].push(metric)
    }

    // Sort each group by iteration
    for (const name in groups) {
      groups[name].sort((a, b) => a.iteration - b.iteration)
    }

    return groups
  }, [parsedMetrics])

  const hasMetrics = Object.keys(metricsByName).length > 0

  return (
    <div className={clsx('flex flex-col', className)}>
      <div className="p-4 border-b border-tv-border">
        <h3 className="text-sm font-semibold text-tv-text">Metrics</h3>
        <p className="text-xs text-tv-muted mt-1">
          Auto-extracted from agent output
        </p>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!hasMetrics ? (
          <div className="text-center text-tv-muted py-8">
            <div className="text-sm">No metrics detected yet</div>
            <div className="text-xs mt-1">
              Looking for patterns: {metrics.map(m => m.name).join(', ')}
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {metrics.map(metric => {
              const values = metricsByName[metric.name] || []
              return (
                <MetricCard
                  key={metric.name}
                  name={metric.name}
                  values={values}
                  higherIsBetter={metric.higher_is_better}
                />
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
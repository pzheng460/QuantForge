import { useState } from 'react'
import { cn } from '@/lib/utils'
import { ChevronDown } from 'lucide-react'
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import type { SchemaField, StrategySchema, Exchange } from '../types'

// --- Field input -------------------------------------------------------------

function FieldInput({
  field,
  value,
  onChange,
}: {
  field: SchemaField
  value: unknown
  onChange: (v: unknown) => void
}) {
  if (field.type === 'bool') {
    return (
      <label className="flex items-center justify-between gap-2 cursor-pointer py-1">
        <span className="text-xs text-muted-foreground">{field.label}</span>
        <div
          className={cn(
            'relative w-8 h-4 rounded-full transition-colors cursor-pointer',
            Boolean(value) ? 'bg-primary' : 'bg-border',
          )}
          onClick={() => onChange(!Boolean(value))}
        >
          <span
            className={cn(
              'absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform',
              Boolean(value) ? 'translate-x-4' : 'translate-x-0.5',
            )}
          />
        </div>
      </label>
    )
  }

  if (field.type === 'int' || field.type === 'float') {
    return (
      <div className="flex flex-col gap-0.5 py-1">
        <Label>{field.label}</Label>
        <Input
          type="number"
          className="text-xs py-1 h-7"
          value={value as number}
          step={field.step ?? (field.type === 'int' ? 1 : 0.01)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          onChange={(e) =>
            onChange(field.type === 'int' ? parseInt(e.target.value) : parseFloat(e.target.value))
          }
        />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0.5 py-1">
      <Label className="text-[10px]">{field.label}</Label>
      <Input
        type="text"
        className="text-xs py-1 h-7"
        value={value as string}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

// --- Collapsible section ─────────────────────────────────────────────────────

function Section({
  title,
  defaultOpen = true,
  children,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="border-b border-border">
      <CollapsibleTrigger className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider hover:text-foreground transition-colors">
        {title}
        <ChevronDown
          className={cn('h-3.5 w-3.5 transition-transform', open && 'rotate-180')}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-3 pb-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  )
}

// --- Props -------------------------------------------------------------------

interface Props {
  strategies: StrategySchema[]
  exchanges: Exchange[]
  strategy: string
  exchange: string
  symbol: string
  period: string
  leverage: number
  mesaIndex: number
  useDateRange: boolean
  startDate: string
  endDate: string
  configOverride: Record<string, string | number | boolean>
  filterOverride: Record<string, string | number | boolean>
  loading: boolean
  status: string
  onStrategyChange: (v: string) => void
  onExchangeChange: (v: string) => void
  onSymbolChange: (v: string) => void
  onPeriodChange: (v: string) => void
  onLeverageChange: (v: number) => void
  onMesaIndexChange: (v: number) => void
  onUseDateRangeChange: (v: boolean) => void
  onStartDateChange: (v: string) => void
  onEndDateChange: (v: string) => void
  onConfigChange: (v: Record<string, string | number | boolean>) => void
  onFilterChange: (v: Record<string, string | number | boolean>) => void
  onRun: () => void
}

const PERIODS = ['1w', '1m', '3m', '6m', '1y', '2y', '3y', '5y']

const STATUS_COLOR: Record<string, string> = {
  pending: 'text-yellow-400',
  running: 'text-primary animate-pulse',
  completed: 'text-tv-green',
  failed: 'text-tv-red',
}

export default function ParameterPanel({
  strategies,
  exchanges,
  strategy,
  exchange,
  symbol,
  period,
  leverage,
  mesaIndex,
  useDateRange,
  startDate,
  endDate,
  configOverride,
  filterOverride,
  loading,
  status,
  onStrategyChange,
  onExchangeChange,
  onSymbolChange,
  onPeriodChange,
  onLeverageChange,
  onMesaIndexChange,
  onUseDateRangeChange,
  onStartDateChange,
  onEndDateChange,
  onConfigChange,
  onFilterChange,
  onRun,
}: Props) {
  const selectedSchema = strategies.find((s) => s.name === strategy)
  const selectedExchange = exchanges.find((e) => e.id === exchange)

  return (
    <div className="flex flex-col h-full bg-card border-r border-border">
      {/* Header */}
      <div className="px-3 py-2 border-b border-border shrink-0">
        <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider">
          Parameters
        </span>
      </div>

      {/* Sections */}
      <div className="flex-1 overflow-y-auto">
        {/* Strategy */}
        <Section title="Strategy">
          <div className="space-y-1">
            <div className="flex flex-col gap-0.5 py-1">
              <Label className="text-[10px]">Strategy</Label>
              <Select
                className="text-xs py-1 h-7"
                value={strategy}
                onChange={(e) => onStrategyChange(e.target.value)}
              >
                {strategies.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.display_name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <Label className="text-[10px]">Exchange</Label>
              <Select
                className="text-xs py-1 h-7"
                value={exchange}
                onChange={(e) => onExchangeChange(e.target.value)}
              >
                {exchanges.map((ex) => (
                  <option key={ex.id} value={ex.id}>
                    {ex.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <Label className="text-[10px]">
                Symbol{' '}
                <span className="text-muted-foreground">
                  ({selectedExchange?.id ? selectedExchange.default_symbol : '...'})
                </span>
              </Label>
              <Input
                type="text"
                className="text-xs py-1 h-7"
                placeholder={selectedExchange?.default_symbol}
                value={symbol}
                onChange={(e) => onSymbolChange(e.target.value)}
              />
            </div>
          </div>
        </Section>

        {/* Period */}
        <Section title="Period">
          <div className="space-y-1">
            <label className="flex items-center justify-between gap-2 cursor-pointer py-1">
              <span className="text-xs text-muted-foreground">Custom Date Range</span>
              <div
                className={cn(
                  'relative w-8 h-4 rounded-full transition-colors cursor-pointer',
                  useDateRange ? 'bg-primary' : 'bg-border',
                )}
                onClick={() => onUseDateRangeChange(!useDateRange)}
              >
                <span
                  className={cn(
                    'absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform',
                    useDateRange ? 'translate-x-4' : 'translate-x-0.5',
                  )}
                />
              </div>
            </label>

            {!useDateRange ? (
              <div className="flex flex-col gap-0.5 py-1">
                <Label className="text-[10px]">Period</Label>
                <Select
                  className="text-xs h-7"
                  value={period}
                  onChange={(e) => onPeriodChange(e.target.value)}
                >
                  {PERIODS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label className="text-[10px]">Start Date</Label>
                  <Input
                    type="date"
                    className="text-xs py-1 h-7"
                    value={startDate}
                    onChange={(e) => onStartDateChange(e.target.value)}
                  />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <Label className="text-[10px]">End Date</Label>
                  <Input
                    type="date"
                    className="text-xs py-1 h-7"
                    value={endDate}
                    onChange={(e) => onEndDateChange(e.target.value)}
                  />
                </div>
              </>
            )}
          </div>
        </Section>

        {/* Execution */}
        <Section title="Execution">
          <div className="space-y-1">
            <div className="flex flex-col gap-0.5 py-1">
              <Label>Leverage</Label>
              <Input
                type="number"
                className="text-xs py-1 h-7"
                value={leverage}
                min={1}
                max={20}
                step={1}
                onChange={(e) => onLeverageChange(Number(e.target.value))}
              />
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <Label>Mesa Index</Label>
              <Input
                type="number"
                className="text-xs py-1 h-7"
                value={mesaIndex}
                min={0}
                step={1}
                onChange={(e) => onMesaIndexChange(parseInt(e.target.value))}
              />
            </div>
          </div>
        </Section>

        {/* Strategy config params */}
        {selectedSchema && selectedSchema.config_fields.length > 0 && (
          <Section title="Strategy Parameters">
            {selectedSchema.config_fields.map((f) => (
              <FieldInput
                key={f.name}
                field={f}
                value={configOverride[f.name] ?? f.default}
                onChange={(v) =>
                  onConfigChange({ ...configOverride, [f.name]: v as string | number | boolean })
                }
              />
            ))}
          </Section>
        )}

        {/* Filter params */}
        {selectedSchema && selectedSchema.filter_fields.length > 0 && (
          <Section title="Filter Parameters" defaultOpen={false}>
            <div className="space-y-0">
              {selectedSchema.filter_fields.map((f) => (
                <FieldInput
                  key={f.name}
                  field={f}
                  value={filterOverride[f.name] ?? f.default}
                  onChange={(v) =>
                    onFilterChange({
                      ...filterOverride,
                      [f.name]: v as string | number | boolean,
                    })
                  }
                />
              ))}
            </div>
          </Section>
        )}
      </div>

      {/* Run button at bottom */}
      <div className="shrink-0 border-t border-border p-3 space-y-2">
        {status && (
          <div
            className={cn(
              'text-xs text-center capitalize',
              STATUS_COLOR[status] ?? 'text-muted-foreground',
            )}
          >
            {status === 'running' ? '\u25CF Running...' : status}
          </div>
        )}
        <Button className="w-full" disabled={loading || !strategy} onClick={onRun}>
          {loading ? 'Running...' : '\u25B6 Run Backtest'}
        </Button>
      </div>
    </div>
  )
}

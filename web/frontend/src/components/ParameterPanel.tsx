import { useState } from 'react'
import clsx from 'clsx'
import type { SchemaField, StrategySchema, Exchange } from '../types'

// ─── Field input ──────────────────────────────────────────────────────────────

function FieldInput({ field, value, onChange }: { field: SchemaField; value: unknown; onChange: (v: unknown) => void }) {
  if (field.type === 'bool') {
    return (
      <label className="flex items-center justify-between gap-2 cursor-pointer py-1">
        <span className="text-xs text-tv-muted">{field.label}</span>
        <div
          className={clsx(
            'relative w-8 h-4 rounded-full transition-colors cursor-pointer',
            Boolean(value) ? 'bg-tv-blue' : 'bg-tv-border',
          )}
          onClick={() => onChange(!Boolean(value))}
        >
          <span
            className={clsx(
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
        <label className="text-[10px] text-tv-muted">{field.label}</label>
        <input
          type="number"
          className="tv-input text-xs py-1"
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
      <label className="text-[10px] text-tv-muted">{field.label}</label>
      <input
        type="text"
        className="tv-input text-xs py-1"
        value={value as string}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

// ─── Collapsible section ──────────────────────────────────────────────────────

function Section({ title, children, defaultOpen = true }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border-b border-tv-border">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-semibold text-tv-muted uppercase tracking-wider hover:text-tv-text transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        {title}
        <svg
          className={clsx('w-3 h-3 transition-transform', open && 'rotate-180')}
          viewBox="0 0 12 12"
          fill="currentColor"
        >
          <path d="M6 8L1 3h10z" />
        </svg>
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  )
}

// ─── Props ─────────────────────────────────────────────────────────────────────

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
  running: 'text-tv-blue animate-pulse',
  completed: 'text-tv-green',
  failed: 'text-tv-red',
}

export default function ParameterPanel({
  strategies, exchanges, strategy, exchange, symbol, period, leverage, mesaIndex,
  useDateRange, startDate, endDate, configOverride, filterOverride, loading, status,
  onStrategyChange, onExchangeChange, onSymbolChange, onPeriodChange, onLeverageChange,
  onMesaIndexChange, onUseDateRangeChange, onStartDateChange, onEndDateChange,
  onConfigChange, onFilterChange, onRun,
}: Props) {
  const selectedSchema = strategies.find((s) => s.name === strategy)
  const selectedExchange = exchanges.find((e) => e.id === exchange)

  return (
    <div className="flex flex-col h-full bg-tv-panel border-r border-tv-border overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-tv-border shrink-0">
        <span className="text-[11px] font-semibold text-tv-muted uppercase tracking-wider">Parameters</span>
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        {/* Basic settings */}
        <Section title="Strategy">
          <div className="space-y-1">
            <div className="flex flex-col gap-0.5 py-1">
              <label className="text-[10px] text-tv-muted">Strategy</label>
              <select className="tv-select text-xs py-1" value={strategy} onChange={(e) => onStrategyChange(e.target.value)}>
                {strategies.map((s) => (
                  <option key={s.name} value={s.name}>{s.display_name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <label className="text-[10px] text-tv-muted">Exchange</label>
              <select className="tv-select text-xs py-1" value={exchange} onChange={(e) => onExchangeChange(e.target.value)}>
                {exchanges.map((ex) => (
                  <option key={ex.id} value={ex.id}>{ex.name}</option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <label className="text-[10px] text-tv-muted">
                Symbol <span className="text-tv-border">({selectedExchange?.default_symbol ?? '…'})</span>
              </label>
              <input
                type="text"
                className="tv-input text-xs py-1"
                placeholder={selectedExchange?.default_symbol ?? ''}
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
              <span className="text-xs text-tv-muted">Custom Date Range</span>
              <div
                className={clsx(
                  'relative w-8 h-4 rounded-full transition-colors cursor-pointer',
                  useDateRange ? 'bg-tv-blue' : 'bg-tv-border',
                )}
                onClick={() => onUseDateRangeChange(!useDateRange)}
              >
                <span className={clsx('absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform', useDateRange ? 'translate-x-4' : 'translate-x-0.5')} />
              </div>
            </label>

            {!useDateRange ? (
              <div className="flex flex-col gap-0.5 py-1">
                <label className="text-[10px] text-tv-muted">Period</label>
                <select className="tv-select text-xs py-1" value={period} onChange={(e) => onPeriodChange(e.target.value)}>
                  {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
            ) : (
              <>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">Start Date</label>
                  <input type="date" className="tv-input text-xs py-1" value={startDate} onChange={(e) => onStartDateChange(e.target.value)} />
                </div>
                <div className="flex flex-col gap-0.5 py-1">
                  <label className="text-[10px] text-tv-muted">End Date</label>
                  <input type="date" className="tv-input text-xs py-1" value={endDate} onChange={(e) => onEndDateChange(e.target.value)} />
                </div>
              </>
            )}
          </div>
        </Section>

        {/* Execution */}
        <Section title="Execution">
          <div className="space-y-1">
            <div className="flex flex-col gap-0.5 py-1">
              <label className="text-[10px] text-tv-muted">Leverage</label>
              <input
                type="number"
                className="tv-input text-xs py-1"
                min={1} max={20} step={1}
                value={leverage}
                onChange={(e) => onLeverageChange(Number(e.target.value))}
              />
            </div>
            <div className="flex flex-col gap-0.5 py-1">
              <label className="text-[10px] text-tv-muted">Mesa Index</label>
              <input
                type="number"
                className="tv-input text-xs py-1"
                min={0} step={1}
                value={mesaIndex}
                onChange={(e) => onMesaIndexChange(parseInt(e.target.value))}
              />
            </div>
          </div>
        </Section>

        {/* Strategy params */}
        {selectedSchema && selectedSchema.config_fields.length > 0 && (
          <Section title="Strategy Parameters">
            <div className="space-y-0">
              {selectedSchema.config_fields.map((f) => (
                <FieldInput
                  key={f.name}
                  field={f}
                  value={configOverride[f.name] ?? f.default}
                  onChange={(v) => onConfigChange({ ...configOverride, [f.name]: v as string | number | boolean })}
                />
              ))}
            </div>
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
                  onChange={(v) => onFilterChange({ ...filterOverride, [f.name]: v as string | number | boolean })}
                />
              ))}
            </div>
          </Section>
        )}
      </div>

      {/* Run button at bottom */}
      <div className="shrink-0 border-t border-tv-border p-3 space-y-2">
        {status && (
          <div className={clsx('text-xs text-center capitalize', STATUS_COLOR[status] ?? 'text-tv-muted')}>
            {status === 'running' ? '● Running…' : status}
          </div>
        )}
        <button
          className="tv-btn-primary w-full"
          onClick={onRun}
          disabled={loading || !strategy}
        >
          {loading ? 'Running…' : '▶ Run Backtest'}
        </button>
      </div>
    </div>
  )
}

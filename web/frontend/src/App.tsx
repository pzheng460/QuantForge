import { useState, useRef, useEffect, Suspense, lazy } from 'react'
import { Routes, Route } from 'react-router-dom'
import { NavLink } from 'react-router-dom'
import { Clock, Loader2 } from 'lucide-react'
import { ErrorBoundary } from './components/ErrorBoundary'

const DashboardPage = lazy(() => import('./pages/Dashboard'))
const BacktestPage = lazy(() => import('./pages/Backtest'))
const OptimizerPage = lazy(() => import('./pages/Optimizer'))
import { TimezoneProvider, useTimezone } from './hooks/useTimezone'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

const COMMON_TZ = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Asia/Hong_Kong',
  'Asia/Seoul',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Pacific/Auckland',
]

function tzLabel(tz: string): string {
  const parts = tz.split('/')
  return parts[parts.length - 1].replace(/_/g, ' ')
}

function tzOffset(tz: string): string {
  const now = new Date()
  const fmt = new Intl.DateTimeFormat('en', { timeZone: tz, timeZoneName: 'shortOffset' })
  const parts = fmt.formatToParts(now)
  return parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
}

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'px-3 py-1.5 rounded-sm text-xs font-medium transition-colors',
          isActive
            ? 'bg-primary text-primary-foreground'
            : 'text-muted-foreground hover:text-foreground hover:bg-secondary',
        )
      }
    >
      {label}
    </NavLink>
  )
}

function TimezoneSelector() {
  const { timezone, setTimezone, localTz } = useTimezone()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function getAllTimezones(): string[] {
    try {
      return (Intl as unknown as { supportedValuesOf(key: string): string[] }).supportedValuesOf('timeZone')
    } catch {
      return COMMON_TZ
    }
  }

  const allTz = getAllTimezones()
  const filtered = search
    ? allTz.filter((tz) => tz.toLowerCase().includes(search.toLowerCase()))
    : COMMON_TZ

  return (
    <div ref={ref} className="relative">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen(!open)}
        className="gap-1 text-[11px] text-muted-foreground hover:text-foreground h-7"
      >
        <Clock className="h-3 w-3" />
        <span className="tabular-nums">{tzOffset(timezone)}</span>
        <span className="max-w-[80px] truncate">{tzLabel(timezone)}</span>
      </Button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-64 rounded-md border bg-popover text-popover-foreground shadow-md z-50 overflow-hidden">
          <div className="p-2 border-b">
            <Input
              placeholder="Search timezone..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="h-7 text-xs"
              autoFocus
            />
          </div>
          <div className="overflow-y-auto max-h-60">
            {!search && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { setTimezone(localTz); setOpen(false); setSearch('') }}
                className={cn(
                  'w-full justify-between rounded-none h-auto px-3 py-1.5 text-xs transition-colors',
                  timezone === localTz && 'text-primary font-medium',
                )}
              >
                <span>Local ({tzLabel(localTz)})</span>
                <span className="text-muted-foreground tabular-nums">{tzOffset(localTz)}</span>
              </Button>
            )}
            {filtered.map((tz) => (
              <Button
                variant="ghost"
                size="sm"
                key={tz}
                onClick={() => { setTimezone(tz); setOpen(false); setSearch('') }}
                className={cn(
                  'w-full justify-between rounded-none h-auto px-3 py-1.5 text-xs transition-colors',
                  timezone === tz ? 'text-primary font-medium' : 'text-foreground',
                )}
              >
                <span className="truncate mr-2">{tz}</span>
                <span className="text-muted-foreground tabular-nums shrink-0">{tzOffset(tz)}</span>
              </Button>
            ))}
            {filtered.length === 0 && (
              <div className="px-3 py-4 text-center text-xs text-muted-foreground">No matching timezone</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AppContent() {
  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="bg-card border-b px-4 py-2 flex items-center gap-1 shrink-0">
        <span className="text-primary font-bold text-sm mr-4 flex items-center gap-1">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 13h2v-2H3v2zm0-4h2V7H3v2zm0 8h2v-2H3v2zm4-4h14v-2H7v2zm0-4V7h14v2H7zm0 8h14v-2H7v2z" />
          </svg>
          QuantForge
        </span>
        <nav className="flex items-center gap-0.5">
          <NavItem to="/" label="Live Trading" />
          <NavItem to="/backtest" label="Backtest" />
          <NavItem to="/optimizer" label="Optimizer" />
        </nav>
        <div className="ml-auto">
          <TimezoneSelector />
        </div>
      </header>
      <main className="flex-1 overflow-hidden">
        <ErrorBoundary>
          <Suspense fallback={
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          }>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/backtest" element={<BacktestPage />} />
              <Route path="/optimizer" element={<OptimizerPage />} />
            </Routes>
          </Suspense>
        </ErrorBoundary>
      </main>
    </div>
  )
}

export default function App() {
  return (
    <TimezoneProvider>
      <AppContent />
    </TimezoneProvider>
  )
}

import { useState, useRef, useEffect, Suspense, lazy } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import { Clock, Loader2, Search } from 'lucide-react'
import { ErrorBoundary } from './components/ErrorBoundary'

const DashboardPage = lazy(() => import('./pages/Dashboard'))
const BacktestPage = lazy(() => import('./pages/Backtest'))
const OptimizerPage = lazy(() => import('./pages/Optimizer'))
import { TimezoneProvider, useTimezone } from './hooks/useTimezone'
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
      end={to === '/'}
      className={({ isActive }) =>
        cn('nav-pill', isActive && 'bg-accent text-foreground')
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
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          'inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md',
          'text-[12px] font-medium text-muted-foreground hover:text-foreground hover:bg-accent',
          'transition-colors',
        )}
      >
        <Clock className="h-3.5 w-3.5" strokeWidth={2} />
        <span className="font-mono tabular-nums text-[11px]">{tzOffset(timezone)}</span>
        <span className="max-w-[90px] truncate">{tzLabel(timezone)}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-72 rounded-lg border bg-popover text-popover-foreground shadow-lg z-50 overflow-hidden animate-fade-in">
          <div className="p-2 border-b border-border">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder="Search timezone..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="h-8 text-[13px] pl-7"
                autoFocus
              />
            </div>
          </div>
          <div className="overflow-y-auto max-h-64 p-1">
            {!search && (
              <button
                type="button"
                onClick={() => { setTimezone(localTz); setOpen(false); setSearch('') }}
                className={cn(
                  'w-full flex items-center justify-between px-2 py-1.5 rounded-md text-[13px] transition-colors',
                  'hover:bg-accent',
                  timezone === localTz ? 'text-foreground font-medium' : 'text-foreground',
                )}
              >
                <span className="flex items-center gap-2">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Local</span>
                  <span>{tzLabel(localTz)}</span>
                </span>
                <span className="text-muted-foreground tabular-nums font-mono text-[11px]">{tzOffset(localTz)}</span>
              </button>
            )}
            {filtered.map((tz) => {
              const active = timezone === tz
              return (
                <button
                  type="button"
                  key={tz}
                  onClick={() => { setTimezone(tz); setOpen(false); setSearch('') }}
                  className={cn(
                    'w-full flex items-center justify-between px-2 py-1.5 rounded-md text-[13px] transition-colors',
                    'hover:bg-accent',
                    active && 'bg-accent text-foreground font-medium',
                  )}
                >
                  <span className="truncate mr-2">{tz}</span>
                  <span className="text-muted-foreground tabular-nums font-mono text-[11px] shrink-0">{tzOffset(tz)}</span>
                </button>
              )
            })}
            {filtered.length === 0 && (
              <div className="px-3 py-6 text-center text-[12px] text-muted-foreground">No matching timezone</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function Logo() {
  return (
    <NavLink to="/" end className="group inline-flex items-center select-none">
      <span className="text-[14.5px] font-semibold tracking-tight text-foreground">QuantForge</span>
    </NavLink>
  )
}

function TopBar() {
  return (
    <header className="sticky top-0 z-40 shrink-0 bg-background/85 backdrop-blur-md supports-[backdrop-filter]:bg-background/70 border-b border-border">
      <div className="px-5 lg:px-6 h-12 flex items-center gap-5">
        <Logo />

        <div className="hidden sm:flex items-center text-[11px] font-mono text-muted-foreground/80">
          <span className="px-1 text-muted-foreground/50">/</span>
          <span className="tracking-tight">workspace</span>
        </div>

        <nav className="flex items-center gap-0.5 ml-2">
          <NavItem to="/" label="Live" />
          <NavItem to="/backtest" label="Backtest" />
          <NavItem to="/optimizer" label="Optimizer" />
        </nav>

        <div className="ml-auto flex items-center gap-1.5">
          <TimezoneSelector />
          <span className="hidden md:inline-flex items-center gap-1.5 h-7 px-2 rounded-md text-[11px] font-mono text-muted-foreground border border-border bg-card">
            <span className="status-dot" />
            <span className="tracking-tight">connected</span>
          </span>
        </div>
      </div>
    </header>
  )
}

function AppContent() {
  return (
    <div className="flex h-screen flex-col bg-surface overflow-hidden">
      <TopBar />
      <main className="flex-1 min-h-0 overflow-hidden animate-fade-in">
        <ErrorBoundary>
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full gap-2.5 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-[12px] font-medium">Loading…</span>
              </div>
            }
          >
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

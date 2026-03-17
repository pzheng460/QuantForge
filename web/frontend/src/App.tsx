import { useState, useRef, useEffect } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import BacktestPage from './pages/Backtest'
import DashboardPage from './pages/Dashboard'
import OptimizerPage from './pages/Optimizer'
import { TimezoneProvider, useTimezone } from './hooks/useTimezone'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `px-3 py-1.5 rounded-sm text-xs font-medium transition-colors ${
          isActive
            ? 'bg-tv-blue text-white'
            : 'text-tv-muted hover:text-tv-text hover:bg-tv-border'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

// Common timezones (short list for quick access)
const COMMON_TZ = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Moscow',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Asia/Hong_Kong',
  'Asia/Seoul',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Pacific/Auckland',
]

// Get all IANA timezones
function getAllTimezones(): string[] {
  try {
    return (Intl as unknown as { supportedValuesOf(key: string): string[] }).supportedValuesOf('timeZone')
  } catch {
    return COMMON_TZ
  }
}

/** Short display label: "Asia/Shanghai" → "Shanghai", "UTC" → "UTC" */
function tzLabel(tz: string): string {
  const parts = tz.split('/')
  return parts[parts.length - 1].replace(/_/g, ' ')
}

/** UTC offset string like "UTC+8" */
function tzOffset(tz: string): string {
  const now = new Date()
  const fmt = new Intl.DateTimeFormat('en', { timeZone: tz, timeZoneName: 'shortOffset' })
  const parts = fmt.formatToParts(now)
  const offset = parts.find(p => p.type === 'timeZoneName')?.value ?? ''
  return offset
}

function TimezoneSelector() {
  const { timezone, setTimezone, localTz } = useTimezone()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const allTz = getAllTimezones()
  const filtered = search
    ? allTz.filter(tz => tz.toLowerCase().includes(search.toLowerCase()))
    : COMMON_TZ

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 px-2 py-1 text-[11px] text-tv-muted hover:text-tv-text rounded-sm hover:bg-tv-border transition-colors"
        title={`Timezone: ${timezone}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="10" />
          <path d="M12 6v6l4 2" />
        </svg>
        <span className="tabular-nums">{tzOffset(timezone)}</span>
        <span className="max-w-[80px] truncate">{tzLabel(timezone)}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-64 bg-tv-panel border border-tv-border rounded shadow-lg z-50 max-h-80 flex flex-col">
          <div className="p-2 border-b border-tv-border">
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search timezone..."
              className="w-full bg-tv-bg border border-tv-border rounded px-2 py-1 text-xs text-tv-text placeholder-tv-muted outline-none focus:border-tv-blue"
              autoFocus
            />
          </div>
          <div className="overflow-y-auto flex-1">
            {/* Local timezone shortcut */}
            {!search && (
              <button
                onClick={() => { setTimezone(localTz); setOpen(false); setSearch('') }}
                className={`w-full text-left px-3 py-1.5 text-xs hover:bg-tv-border transition-colors flex items-center justify-between ${
                  timezone === localTz ? 'text-tv-blue font-medium' : 'text-tv-text'
                }`}
              >
                <span>Local ({tzLabel(localTz)})</span>
                <span className="text-tv-muted tabular-nums">{tzOffset(localTz)}</span>
              </button>
            )}
            {filtered.map(tz => (
              <button
                key={tz}
                onClick={() => { setTimezone(tz); setOpen(false); setSearch('') }}
                className={`w-full text-left px-3 py-1.5 text-xs hover:bg-tv-border transition-colors flex items-center justify-between ${
                  timezone === tz ? 'text-tv-blue font-medium' : 'text-tv-text'
                }`}
              >
                <span className="truncate mr-2">{tz}</span>
                <span className="text-tv-muted tabular-nums shrink-0">{tzOffset(tz)}</span>
              </button>
            ))}
            {filtered.length === 0 && (
              <div className="px-3 py-4 text-center text-xs text-tv-muted">No matching timezone</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function AppContent() {
  return (
    <div className="min-h-screen flex flex-col bg-tv-bg">
      {/* TV-style top nav bar */}
      <header className="bg-tv-panel border-b border-tv-border px-4 py-2 flex items-center gap-1 shrink-0">
        <span className="text-tv-blue font-bold text-sm mr-4 flex items-center gap-1">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M3 13h2v-2H3v2zm0 4h2v-2H3v2zm0-8h2V7H3v2zm4 4h14v-2H7v2zm0 4h14v-2H7v2zM7 7v2h14V7H7z"/>
          </svg>
          QuantForge
        </span>
        <div className="flex items-center gap-0.5">
          <NavItem to="/" label="Live Trading" />
          <NavItem to="/backtest" label="Backtest" />
          <NavItem to="/optimizer" label="Optimizer" />
        </div>
        <div className="ml-auto">
          <TimezoneSelector />
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={
            <div className="px-6 py-6 max-w-screen-2xl mx-auto w-full">
              <DashboardPage />
            </div>
          } />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/optimizer" element={
            <div className="px-6 py-6 max-w-screen-2xl mx-auto w-full">
              <OptimizerPage />
            </div>
          } />
        </Routes>
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

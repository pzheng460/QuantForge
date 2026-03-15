import { createContext, useContext, useState, type ReactNode } from 'react'

const LOCAL_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone
const STORAGE_KEY = 'qf_timezone'

function getInitialTz(): string {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) return saved
  } catch { /* ignore */ }
  return LOCAL_TZ
}

interface TimezoneCtx {
  timezone: string
  setTimezone: (tz: string) => void
  localTz: string
}

const Ctx = createContext<TimezoneCtx>({
  timezone: LOCAL_TZ,
  setTimezone: () => {},
  localTz: LOCAL_TZ,
})

export function TimezoneProvider({ children }: { children: ReactNode }) {
  const [timezone, _setTz] = useState(getInitialTz)

  const setTimezone = (tz: string) => {
    _setTz(tz)
    try { localStorage.setItem(STORAGE_KEY, tz) } catch { /* ignore */ }
  }

  return (
    <Ctx.Provider value={{ timezone, setTimezone, localTz: LOCAL_TZ }}>
      {children}
    </Ctx.Provider>
  )
}

export function useTimezone() {
  return useContext(Ctx)
}

/** Format an ISO timestamp as "Mon DD, YYYY, HH:mm" in the given timezone */
export function fmtDateTz(iso: string | undefined, tz: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  const mon = d.toLocaleString('en', { month: 'short', timeZone: tz })
  const day = d.toLocaleString('en', { day: '2-digit', timeZone: tz })
  const year = d.toLocaleString('en', { year: 'numeric', timeZone: tz })
  const h = d.toLocaleString('en', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz })
  return `${mon} ${day}, ${year}, ${h}`
}

/** Format an ISO timestamp as "YYYY-MM-DD" in the given timezone */
export function fmtDateShortTz(iso: string | undefined, tz: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '—'
  return d.toLocaleDateString('en-CA', { timeZone: tz }) // en-CA gives YYYY-MM-DD
}

/** Format an ISO timestamp as "YYYY-MM-DD HH:mm:ss" in the given timezone */
export function fmtTimeTz(iso: string | undefined, tz: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '-'
  const date = d.toLocaleDateString('en-CA', { timeZone: tz })
  const time = d.toLocaleTimeString('en-GB', { timeZone: tz, hour12: false })
  return `${date} ${time}`
}

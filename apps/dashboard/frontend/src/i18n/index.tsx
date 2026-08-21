import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

import appZh from './zh/pages/app'
import dashboardZh from './zh/pages/dashboard'
import backtestZh from './zh/pages/backtest'
import optimizerZh from './zh/pages/optimizer'
import researchZh from './zh/pages/research'
import schwabZh from './zh/pages/schwab'
import strategytesterZh from './zh/pages/strategytester'

import appEn from './en/pages/app'
import dashboardEn from './en/pages/dashboard'
import backtestEn from './en/pages/backtest'
import optimizerEn from './en/pages/optimizer'
import researchEn from './en/pages/research'
import schwabEn from './en/pages/schwab'
import strategytesterEn from './en/pages/strategytester'

type Locale = 'zh' | 'en'
type Dict = Record<string, string>

const ZH: Dict = {
  ...appZh, ...dashboardZh, ...backtestZh, ...optimizerZh,
  ...researchZh, ...schwabZh, ...strategytesterZh,
}
const EN: Dict = {
  ...appEn, ...dashboardEn, ...backtestEn, ...optimizerEn,
  ...researchEn, ...schwabEn, ...strategytesterEn,
}

const STORAGE_KEY = 'qf_lang'

interface LangCtx {
  locale: Locale
  setLocale: (l: Locale) => void
  /** translate a namespaced key; falls back to en, then the key itself */
  t: (key: string) => string
}

const Ctx = createContext<LangCtx | null>(null)

export function LangProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>(() => {
    if (typeof localStorage !== 'undefined') {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved === 'en' || saved === 'zh') return saved
    }
    return 'zh'
  })
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, locale) } catch { /* ignore */ }
  }, [locale])

  const t = (key: string): string => (locale === 'zh' ? ZH[key] : EN[key]) ?? EN[key] ?? key

  return <Ctx.Provider value={{ locale, setLocale, t }}>{children}</Ctx.Provider>
}

export function useLang(): LangCtx {
  const v = useContext(Ctx)
  if (!v) throw new Error('useLang must be used within <LangProvider>')
  return v
}

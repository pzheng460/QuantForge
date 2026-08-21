import { useEffect, useRef, useState, type ReactNode } from 'react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { ResizeHandle } from '@/components/ResizeHandle'

interface Props {
  /** Initial sidebar width in pixels. */
  defaultWidth?: number
  /** Minimum draggable width. */
  minWidth?: number
  /** Maximum draggable width. */
  maxWidth?: number
  children: ReactNode
}

/** One shared storage key so the sidebar width is synchronized across all tabs. */
const STORAGE_KEY = 'sidebar-width:app'

function readStoredWidth(defaultWidth: number, minWidth: number, maxWidth: number): number {
  if (typeof window === 'undefined') return defaultWidth
  const stored = Number(window.localStorage.getItem(STORAGE_KEY))
  return Number.isFinite(stored) && stored >= minWidth && stored <= maxWidth ? stored : defaultWidth
}

/**
 * Wraps shadcn's SidebarProvider with a draggable right-edge handle so users
 * can resize the sidebar. The width is shared app-wide (single localStorage
 * key + storage-event listener), so every tab keeps the same sidebar width.
 *
 * The handle is rendered absolutely positioned at `left: var(--sidebar-width)`,
 * spanning the full height. A 6px hit-area gives a comfortable click target
 * while the visible bar is only 1px wide; the bar grows to a brand-blue 2px
 * stripe on hover/active.
 */
export function ResizableSidebarShell({
  defaultWidth = 288,
  minWidth = 240,
  maxWidth = 560,
  children,
}: Props) {
  const [width, setWidth] = useState<number>(() => readStoredWidth(defaultWidth, minWidth, maxWidth))
  const draggingRef = useRef(false)

  useEffect(() => {
    function onMove(e: PointerEvent) {
      if (!draggingRef.current) return
      // SidebarProvider is a top-level flex row; we treat clientX directly as the
      // sidebar's right edge since the sidebar starts at x=0 within the provider.
      // The provider is offset below the top bar but unaffected horizontally.
      const next = Math.min(maxWidth, Math.max(minWidth, e.clientX))
      setWidth(next)
    }
    function onUp() {
      if (!draggingRef.current) return
      draggingRef.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [minWidth, maxWidth])

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, String(width))
    } catch {
      /* localStorage may be unavailable */
    }
  }, [width])

  // Keep in sync across browser tabs: another tab's resize changes the shared
  // key, and any mounted shell (or a freshly mounted one) adopts the value.
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key !== STORAGE_KEY) return
      const next = readStoredWidth(defaultWidth, minWidth, maxWidth)
      if (next !== width) setWidth(next)
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [defaultWidth, minWidth, maxWidth, width])

  function startDrag(e: React.PointerEvent) {
    e.preventDefault()
    draggingRef.current = true
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  return (
    <SidebarProvider
      defaultOpen
      style={{ '--sidebar-width': `${width}px` } as React.CSSProperties}
      // h-full + !min-h-0 overrides shadcn's `min-h-svh` so the provider is
      // strictly bounded by its parent. Without this, inner panels with
      // h-full grow with content and overflow the viewport.
      className="relative !min-h-0 h-full overflow-hidden"
    >
      {children}
      <ResizeHandle
        orientation="horizontal"
        onPointerDown={startDrag}
        onDoubleClick={() => setWidth(defaultWidth)}
        className="absolute top-0 bottom-0 z-30 -translate-x-1/2"
        style={{ left: `${width}px` }}
      />
    </SidebarProvider>
  )
}

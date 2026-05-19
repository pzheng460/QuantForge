import { useEffect, useState } from 'react'
import { AlertTriangle, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface Props {
  open: boolean
  strategy: string
  exchange: string
  symbol: string
  positionSize: number
  leverage: number
  onCancel: () => void
  /** Called only after the user types the exact strategy name to confirm. */
  onConfirm: () => void
}

/**
 * Last-chance modal before submitting a LIVE (non-demo) Pine engine.
 * - Red destructive styling so it can't be mistaken for the normal flow.
 * - User must type the exact strategy name; the Start button is disabled
 *   until the input matches.
 * - Escape / clicking the backdrop cancels.
 */
export function LiveSafetyModal({
  open,
  strategy,
  exchange,
  symbol,
  positionSize,
  leverage,
  onCancel,
  onConfirm,
}: Props) {
  const [typed, setTyped] = useState('')

  useEffect(() => {
    if (open) setTyped('')
  }, [open])

  useEffect(() => {
    if (!open) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onEsc)
    return () => document.removeEventListener('keydown', onEsc)
  }, [open, onCancel])

  if (!open) return null

  const matches = typed.trim() === strategy

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-foreground/40 backdrop-blur-sm animate-fade-in"
      onClick={onCancel}
    >
      <div
        className="relative w-full max-w-md mx-4 rounded-lg border-2 border-destructive bg-card shadow-lg overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Red header strip */}
        <div className="flex items-center gap-2 px-4 py-3 bg-destructive text-destructive-foreground">
          <AlertTriangle className="h-5 w-5 shrink-0" />
          <h3 className="text-sm font-semibold flex-1">
            You're about to start a LIVE engine
          </h3>
          <button
            type="button"
            onClick={onCancel}
            className="p-1 rounded hover:bg-white/10 transition-colors"
            aria-label="Cancel"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-3 text-[13px]">
          <p className="leading-snug">
            This will place <span className="font-semibold text-destructive">real orders</span> on{' '}
            <span className="font-mono">{exchange}</span> with{' '}
            <span className="font-mono">{symbol}</span>, position size{' '}
            <span className="font-mono">{positionSize} USDT</span>
            {leverage > 1 && (
              <>
                {' '}× <span className="font-mono">{leverage}x leverage</span>
              </>
            )}
            . Losses are real and uncapped by the dashboard.
          </p>

          <div className="rounded border border-border bg-muted/40 p-3">
            <p className="text-[11px] text-muted-foreground mb-2">
              Type the strategy name <span className="font-mono text-foreground">{strategy}</span> to confirm:
            </p>
            <Input
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={strategy}
              className="font-mono"
            />
          </div>

          <div className="flex items-center justify-end gap-2 pt-1">
            <Button variant="ghost" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={!matches}
              onClick={onConfirm}
            >
              Start LIVE engine
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useRef, useEffect } from 'react'
import { Sparkles, Power } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface EvolvingState {
  enabled: boolean
  strategies: string[]
  updated_at: string
}

export function EvolvingBadge() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useQuery<EvolvingState>({
    queryKey: ['evolving'],
    queryFn: async () => {
      const r = await fetch('/api/bot/evolving')
      if (!r.ok) throw new Error('failed to load evolving state')
      return r.json()
    },
    refetchInterval: 30_000,
  })

  const toggle = useMutation({
    mutationFn: async (next: boolean) => {
      const r = await fetch('/api/bot/evolving', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return r.json()
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['evolving'] }),
  })

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const enabled = !!data?.enabled
  if (isLoading) return null

  return (
    <div ref={popoverRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cn(
          'inline-flex items-center gap-1.5 h-7 px-2 rounded-md',
          'text-[11px] font-mono transition-colors border',
          enabled
            ? 'border-brand/30 bg-brand-soft text-brand hover:bg-brand-soft'
            : 'border-border bg-card text-muted-foreground hover:text-foreground',
        )}
        title={enabled ? 'Evolving Mode is ON' : 'Evolving Mode is OFF (default)'}
      >
        <Sparkles className={cn('h-3 w-3', enabled && 'animate-pulse')} />
        <span className="tracking-tight uppercase">
          evolving {enabled ? 'on' : 'off'}
        </span>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1.5 w-80 rounded-lg border bg-popover text-popover-foreground shadow-lg z-50 p-4 animate-fade-in">
          <div className="flex items-start gap-3">
            <div className={cn(
              'mt-0.5 h-8 w-8 rounded-md flex items-center justify-center shrink-0',
              enabled ? 'bg-brand-soft text-brand' : 'bg-muted text-muted-foreground',
            )}>
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold">Evolving Mode</h3>
              <p className="text-[11.5px] text-muted-foreground mt-0.5 leading-snug">
                Autonomous loop that re-optimises strategies on a schedule and can
                pause/reduce live engines based on risk gates. OFF by default.
              </p>
            </div>
          </div>

          {data && data.strategies.length > 0 && (
            <div className="mt-3 pt-3 border-t border-border">
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                Strategies under control
              </div>
              <div className="flex flex-wrap gap-1">
                {data.strategies.map((s) => (
                  <span key={s} className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-muted">
                    {s}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div className="mt-3 pt-3 border-t border-border flex items-center justify-between gap-2">
            <span className="text-[10.5px] text-muted-foreground">
              Updated {data?.updated_at?.slice(0, 19).replace('T', ' ')}
            </span>
            <Button
              size="sm"
              variant={enabled ? 'destructive' : 'brand'}
              onClick={() => toggle.mutate(!enabled)}
              disabled={toggle.isPending}
            >
              <Power className="mr-1 h-3 w-3" />
              {enabled ? 'Disable' : 'Enable'}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}

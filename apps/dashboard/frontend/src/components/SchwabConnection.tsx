import { useEffect, useState } from 'react'

import { api } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface Account {
  account_hash: string
  account_type: string
  display_id: string
}

export function SchwabConnection() {
  const [status, setStatus] = useState<{ configured: boolean; authenticated: boolean; account_selected?: boolean; detail?: string }>()
  const [accounts, setAccounts] = useState<Account[]>([])
  const [selected, setSelected] = useState('')
  const [error, setError] = useState('')

  const refresh = async () => {
    try {
      const next = await api.schwabStatus()
      setStatus(next)
      if (next.authenticated) setAccounts(await api.schwabAccounts())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  useEffect(() => {
    let active = true
    api.schwabStatus()
      .then(async (next) => {
        const nextAccounts = next.authenticated ? await api.schwabAccounts() : []
        if (active) {
          setStatus(next)
          setAccounts(nextAccounts)
        }
      })
      .catch((err: unknown) => {
        if (active) setError(err instanceof Error ? err.message : String(err))
      })
    return () => { active = false }
  }, [])

  const connect = async () => {
    try {
      const { authorization_url } = await api.schwabAuthStart()
      window.location.assign(authorization_url)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const choose = async (accountHash: string) => {
    setSelected(accountHash)
    try {
      await api.selectSchwabAccount(accountHash)
      await refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="space-y-2 rounded border border-border bg-muted/30 p-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium">Schwab connection</span>
        <Badge variant={status?.authenticated ? 'success' : 'secondary'} className="text-[9px]">
          {status?.authenticated ? 'CONNECTED' : 'NOT CONNECTED'}
        </Badge>
      </div>
      {!status?.configured && <p className="text-[10px] text-destructive">{status?.detail}</p>}
      {!status?.authenticated ? (
        <Button type="button" size="sm" className="w-full h-7" disabled={!status?.configured} onClick={connect}>
          Connect Charles Schwab
        </Button>
      ) : (
        <Select value={selected} onValueChange={choose}>
          <SelectTrigger className="h-7 text-xs"><SelectValue placeholder={status.account_selected ? 'Account selected' : 'Select account'} /></SelectTrigger>
          <SelectContent>
            {accounts.map((account) => (
              <SelectItem key={account.account_hash} value={account.account_hash}>
                {account.account_type} ••••{account.display_id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {error && <p className="text-[10px] text-destructive">{error}</p>}
      <p className="text-[9px] text-muted-foreground">Demo uses local paper trading. Real orders require LIVE confirmation.</p>
    </div>
  )
}

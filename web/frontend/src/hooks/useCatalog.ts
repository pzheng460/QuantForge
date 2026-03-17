import { useEffect } from 'react'
import { api } from '../api/client'
import { useCatalogStore } from '../stores/catalogStore'

/**
 * Shared hook that loads strategies + exchanges once and caches in zustand.
 * All pages call this — the fetch only fires on the first call.
 */
export function useCatalog() {
  const { strategies, exchanges, loaded, setStrategies, setExchanges, setLoaded } =
    useCatalogStore()

  useEffect(() => {
    if (loaded) return
    setLoaded(true) // prevent duplicate fetches
    api.strategies().then(setStrategies)
    api.exchanges().then(setExchanges)
  }, [loaded, setLoaded, setStrategies, setExchanges])

  return { strategies, exchanges }
}

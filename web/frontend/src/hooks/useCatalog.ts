import { useStrategies, useExchanges } from './use-queries'

/**
 * Shared hook that loads strategies + exchanges via React Query.
 * All pages call this — React Query deduplicates and caches automatically.
 */
export function useCatalog() {
  const { data: strategies = [] } = useStrategies()
  const { data: exchanges = [] } = useExchanges()

  return { strategies, exchanges }
}

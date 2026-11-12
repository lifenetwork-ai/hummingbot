import { Button } from '../../shared/components/Button'

interface ErrorSourceInput {
  name: string
  phase: 'loading' | 'ready' | 'error'
  error: Error | null
  data: unknown
  refreshing: boolean
  retry: () => void
}

/**
 * Single alert strip listing every Overview data source whose latest request failed.
 * Sources with retained previous data are marked as "unverified" rather than empty.
 */
export function DataSourceErrorBanner({ sources, className }: { sources: ErrorSourceInput[]; className?: string }) {
  const failed = sources.filter((source) => source.phase === 'error')
  if (failed.length === 0) return null
  return (
    <div className={className} role="alert">
      {failed.map(({ name, error, data, refreshing, retry }) => (
        <div key={name} className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-app-border bg-app-surface p-3 text-sm">
          <div>
            <p className="font-semibold text-app-danger">{name}: API error</p>
            <p className="text-app-muted">{error?.message ?? 'Request failed'}. {data ? 'Previous observations are retained; current state is unverified.' : 'No observation received.'}</p>
          </div>
          <Button variant="secondary" disabled={refreshing} onClick={() => { void retry() }}>Retry {name.toLowerCase()}</Button>
        </div>
      ))}
    </div>
  )
}

export type { ErrorSourceInput }
export type DataSourceErrorBannerProps = { sources: ErrorSourceInput[]; className?: string }

import { Link } from '@tanstack/react-router'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'

type StatusData = components['schemas']['StatusResponseOutput'] | null | undefined

export interface NoSessionNoticeProps {
  status: StatusData
  className?: string
}

/**
 * New-operator onboarding notice: shown only when a fresh status observation confirms
 * the bot has never started a session (no requested and no observed session).
 * Replaces scattered NO DATA badges with one explicit explanation.
 */
export function NoSessionNotice({ status, className }: NoSessionNoticeProps) {
  if (!status || status.running || status.session || status.observed_session) return null
  return (
    <Surface className={className}>
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-app-text">No session has been started yet</h2>
            <Badge tone="neutral">STOPPED</Badge>
          </div>
          <p className="text-sm text-app-muted">
            The indicators below will show NO DATA until the first quoting session runs. Start a session to begin market making.
          </p>
        </div>
        <Link
          to="/session"
          className="inline-flex min-h-10 items-center gap-1 rounded-button bg-app-surface-muted px-3 py-1.5 text-xs font-semibold text-app-action hover:bg-app-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
        >
          Start new session
        </Link>
      </div>
    </Surface>
  )
}

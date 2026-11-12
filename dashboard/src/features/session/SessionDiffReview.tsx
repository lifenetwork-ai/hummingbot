import { ArrowRight, ChevronLeft, Loader2, RefreshCw } from 'lucide-react'
import type { SessionDraftConfig } from './SessionConfigForm'
import type { ConfigurationReviewModel } from '../session-control/configuration-review'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { ProfileSignatureChip } from '../../shared/components/ProfileSignatureChip'
import { Surface } from '../../shared/components/Surface'

export interface SessionDiffReviewProps {
  currentConfig?: {
    marketMode?: string
    selection?: 'fixed' | 'random'
    profileId?: string | null
    profileIds?: string[] | null
    durationMode?: string
    durationSeconds?: number | null
    sessionId?: string
  }
  currentConfigurationAvailable?: boolean
  proposedProfileIds?: string[]
  proposedConfig: SessionDraftConfig
  action?: 'start' | 'switch'
  controlVersion?: number | null
  submitAllowed?: boolean
  blockedDetail?: string
  isSubmitting?: boolean
  onBack: () => void
  onConfirmSubmit: () => void
  configurationReview?: ConfigurationReviewModel | null
}

export function SessionDiffReview({
  currentConfig,
  currentConfigurationAvailable = true,
  proposedConfig,
  proposedProfileIds = [],
  action = 'switch',
  controlVersion,
  isSubmitting = false,
  submitAllowed = true,
  blockedDetail,
  onBack,
  onConfirmSubmit,
}: SessionDiffReviewProps) {
  const currentMarket = currentConfig?.marketMode || null
  const currentProfile = currentConfig?.selection === 'random'
    ? `Random: ${(currentConfig.profileIds ?? []).join(', ') || 'unrecorded pool'}`
    : currentConfig?.profileId || null
  const currentDuration = currentConfig?.durationMode === 'until_stopped'
    ? 'Until stopped'
    : currentConfig?.durationSeconds
      ? `${Math.round(currentConfig.durationSeconds / 60)} min`
      : 'Unknown'

  const proposedDuration = proposedConfig.durationMode === 'until_stopped'
    ? 'Until stopped'
    : proposedConfig.durationSeconds
      ? `${Math.round(proposedConfig.durationSeconds / 60)} min`
      : 'Default from reviewed catalogue'

  const marketChanged = currentMarket !== proposedConfig.marketMode
  const profileChanged = currentConfig?.selection !== proposedConfig.selection || currentProfile !== (proposedConfig.selection === 'random' ? `Random: ${proposedProfileIds.join(', ')}` : proposedConfig.profileId)
  const durationChanged = currentDuration !== proposedDuration

  return (
    <Surface className="p-5 md:p-6">
      <div className="border-b border-app-border/80 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-heavy text-lg font-bold text-app-text">
            Configuration Diff Review
          </h3>
          <div className="flex items-center gap-2">
            {currentConfig?.sessionId && (
              <div className="flex items-center gap-1 text-xs text-app-muted">
                <span>Session ID:</span>
                <CopyableId id={currentConfig.sessionId} truncateLength={10} copyLabel="Copy session ID" />
              </div>
            )}
            {controlVersion !== null && controlVersion !== undefined && (
              <Badge tone="brand" className="text-xs">
                Control Version: v{controlVersion}
              </Badge>
            )}
          </div>
        </div>
        <p className="mt-1 text-xs text-app-muted">
          Compare pinned configuration against newly proposed configuration before issuing Switch/Start to backend.
        </p>
      </div>

      <div className="mt-5 overflow-hidden rounded-card border border-app-border">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
              <th className="p-3 w-1/3">Property</th>
              <th className="p-3 w-1/3">Pinned Configuration</th>
              <th className="p-3 w-1/3">Proposed Configuration</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60">
            {/* Market Mode Row */}
            <tr className={marketChanged ? 'bg-app-action/5' : ''}>
              <td className="p-3 font-semibold text-app-text">Market Mode</td>
              <td className="p-3 font-mono text-app-muted capitalize">{currentMarket ?? 'No pinned session'}</td>
              <td className="p-3 font-mono font-bold text-app-text capitalize">
                <span className={marketChanged ? 'text-app-action' : ''}>
                  {proposedConfig.marketMode}
                </span>
                {marketChanged && <Badge tone="brand" className="ml-2 text-[10px]">Changed</Badge>}
              </td>
            </tr>

            {/* Profile Row */}
            <tr className={profileChanged ? 'bg-app-action/5' : ''}>
              <td className="p-3 font-semibold text-app-text">Task Profile</td>
              <td className="p-3 font-mono text-app-muted">
                {currentConfig?.profileId ? (
                  <ProfileSignatureChip profileId={currentConfig.profileId} selection={currentConfig.selection} size="sm" />
                ) : (
                  currentProfile ?? 'No pinned configuration'
                )}
              </td>
              <td className="p-3 font-mono font-bold text-app-text">
                <div className="flex items-center gap-1.5">
                  <ProfileSignatureChip
                    profileId={proposedConfig.selection === 'random' ? 'random_pool' : proposedConfig.profileId}
                    selection={proposedConfig.selection}
                    size="sm"
                  />
                  {profileChanged && <Badge tone="brand" className="text-[10px]">Changed</Badge>}
                </div>
              </td>
            </tr>

            {/* Duration Row */}
            <tr className={durationChanged ? 'bg-app-action/5' : ''}>
              <td className="p-3 font-semibold text-app-text">Duration</td>
              <td className="p-3 font-mono text-app-muted">{currentConfig ? currentDuration : 'No pinned configuration'}</td>
              <td className="p-3 font-mono font-bold text-app-text">
                <span className={durationChanged ? 'text-app-action' : ''}>
                  {proposedDuration}
                </span>
                {durationChanged && <Badge tone="brand" className="ml-2 text-[10px]">Changed</Badge>}
              </td>
            </tr>

            {/* Action Intent */}
            <tr>
              <td className="p-3 font-semibold text-app-text">Action Type</td>
              <td className="p-3 text-app-muted">Current Session</td>
              <td className="p-3 font-semibold text-app-action">
                {action === 'start' ? 'Start new session' : 'Switch session'}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {proposedConfig.selection === 'random' && <p className="mt-3 text-xs text-app-muted">Proposed pool from current catalogue: {proposedProfileIds.join(', ') || 'Unknown'}</p>}
      <div className="mt-4 rounded-button bg-app-surface-muted/60 p-3 text-xs text-app-muted">
        <strong>{currentConfigurationAvailable ? 'Pinned configuration from active session. ' : 'Pinned configuration could not be loaded. '}</strong>
        {action === 'switch' ? 'This replaces the active session; held positions are not automatically closed.' : 'This starts a new session with the reviewed configuration.'}
      </div>

      {!submitAllowed && <p role="status" className="mt-3 text-xs text-app-warning">{blockedDetail ?? 'Data or action availability has changed; please refresh and review before submitting.'}</p>}
      <div className="mt-6 flex items-center justify-between gap-3 border-t border-app-border/70 pt-4">
        <Button
          variant="secondary"
          onClick={onBack}
          disabled={isSubmitting}
          className="gap-1.5"
        >
          <ChevronLeft size={15} />
          <span>Back to form</span>
        </Button>

        <Button
          variant="primary"
          onClick={onConfirmSubmit}
          disabled={isSubmitting || !submitAllowed || (action === 'switch' && !currentConfigurationAvailable)}
          className="gap-2 font-bold"
        >
          {isSubmitting ? (
            <>
              <Loader2 size={15} className="animate-spin" />
              <span>Applying configuration…</span>
            </>
          ) : (
            <>
              <RefreshCw size={15} />
              <span>Confirm & Apply New Configuration</span>
              <ArrowRight size={14} />
            </>
          )}
        </Button>
      </div>
    </Surface>
  )
}

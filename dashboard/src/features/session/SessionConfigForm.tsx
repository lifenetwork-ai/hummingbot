import { useEffect, useMemo, useState } from 'react'
import type { components } from '../../core/api/schema'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Input } from '../../shared/components/Input'
import { Select } from '../../shared/components/Select'
import { Surface } from '../../shared/components/Surface'
import { FeedbackDetailsView } from './FeedbackDetailsView'

export interface SessionDraftConfig {
  marketMode: 'spot' | 'perpetual' | 'both' | null
  selection: 'fixed' | 'random'
  profileId: string | null
  durationMode: 'timed' | 'until_stopped'
  durationSeconds: number | null
}

export interface SessionConfigFormProps {
  initialConfig?: Partial<SessionDraftConfig>
  initialAction: 'start' | 'switch'
  actionAvailability: Record<'start' | 'switch', { allowed: boolean; detail: string }>
  activeSessionId?: string | null
  validationErrors?: Record<string, string>
  operationBlockedReason?: string | null
  profilesData?: components['schemas']['ProfilesResponseOutput'] | null
  isLoading?: boolean
  isSubmitting?: boolean
  onSubmitReview: (action: 'start' | 'switch', draft: SessionDraftConfig) => void
  feedbackModel?: FormFeedbackModel | null
  onRetry?: () => void
  onRefresh?: () => void
  onReview?: () => void
}

export function SessionConfigForm({
  initialConfig,
  initialAction,
  actionAvailability,
  activeSessionId,
  validationErrors = {},
  operationBlockedReason,
  profilesData,
  isLoading,
  isSubmitting,
  onSubmitReview,
  feedbackModel,
  onRetry,
  onRefresh,
  onReview,
}: SessionConfigFormProps) {
  const [action, setAction] = useState<'start' | 'switch'>(initialAction)
  const [marketMode, setMarketMode] = useState<'spot' | 'perpetual' | 'both' | null>(initialConfig?.marketMode ?? null)
  const [selection, setSelection] = useState<'fixed' | 'random'>(initialConfig?.selection ?? 'fixed')
  const [profileId, setProfileId] = useState<string | null>(initialConfig?.profileId ?? null)
  const [durationMode, setDurationMode] = useState<'timed' | 'until_stopped' | null>(initialConfig?.durationMode ?? null)
  const [durationSeconds, setDurationSeconds] = useState<number | null>(initialConfig?.durationSeconds ?? null)

  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  // Profiles list from catalogue
  const catalogue = profilesData?.catalogue
  const profileEntries = useMemo(() => (catalogue?.profiles ? Object.keys(catalogue.profiles) : []), [catalogue?.profiles])
  const marketModes = useMemo(() => profilesData?.market_modes ?? [], [profilesData?.market_modes])
  const randomProfiles = catalogue?.random?.profile_ids ?? []
  const minSeconds = catalogue?.min_duration_seconds ?? 0
  const maxSeconds = catalogue?.max_duration_seconds ?? 0
  const defaultSeconds = catalogue?.default_duration_seconds ?? 0
  const durationModes = useMemo(() => (profilesData?.duration_modes ?? ['timed']).filter((mode) => mode !== 'until_stopped' || catalogue?.allow_until_stopped), [profilesData?.duration_modes, catalogue?.allow_until_stopped])

  useEffect(() => {
    if (marketMode === null && marketModes.length > 0) setMarketMode(marketModes[0]!)
    if (durationMode === null && durationModes.length > 0) setDurationMode(durationModes[0]!)
    if (selection === 'fixed' && profileId === null && profileEntries.length > 0) setProfileId(profileEntries[0]!)
  }, [marketMode, marketModes, durationMode, durationModes, selection, profileId, profileEntries])

  const handleValidateAndSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const errors: Record<string, string> = {}

    if (marketMode === null || !marketModes.includes(marketMode)) errors.marketMode = 'Market is not in the current catalogue.'
    if (selection === 'fixed' && (!profileId || !profileEntries.includes(profileId))) errors.profileId = 'Please select a profile present in the catalogue.'
    if (selection === 'random' && randomProfiles.length === 0) errors.selection = 'Catalogue currently has no profiles available for random selection.'
    if (!actionAvailability[action].allowed) errors.action = actionAvailability[action].detail
    if (action === 'switch' && !activeSessionId) errors.action = 'No active session running to switch.'
    if (durationMode === null || !durationModes.includes(durationMode)) errors.durationMode = 'Duration is not in the current catalogue.'
    if (durationMode === 'timed') {
      const requestedSeconds = durationSeconds ?? defaultSeconds
      if (!Number.isSafeInteger(requestedSeconds) || (minSeconds > 0 && requestedSeconds < minSeconds) || (maxSeconds > 0 && requestedSeconds > maxSeconds)) {
        errors.durationSeconds = 'Duration must be within catalogue limits.'
      }
    }

    if (Object.keys(errors).length > 0) {
      setFieldErrors(errors)
      return
    }

    setFieldErrors({})
    if (operationBlockedReason) return
    onSubmitReview(action, {
      marketMode,
      selection,
      profileId: selection === 'fixed' ? profileId : null,
      durationMode: durationMode!,
      durationSeconds: durationMode === 'timed' ? durationSeconds : null,
    })
  }

  return (
    <Surface className="p-5 md:p-6 mt-4">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-border/80 pb-3">
        <div>
          <h3 className="font-heavy text-lg font-bold text-app-text">
            Market Making Session Configuration
          </h3>
        </div>
        {activeSessionId && (
          <div className="flex items-center gap-1.5 text-xs text-app-muted shrink-0">
            <span>Active session:</span>
            <CopyableId id={activeSessionId} truncateLength={10} copyLabel="Copy active session ID" />
          </div>
        )}
      </div>

      <form onSubmit={handleValidateAndSubmit} className="mt-5 space-y-5">
        <div>
          <span className="mb-2 block text-xs font-semibold text-app-text">Session Action</span>
          <div className="grid grid-cols-2 gap-2.5">
            {(['start', 'switch'] as const).map((intent) => (
              <button
                key={intent}
                type="button"
                aria-pressed={action === intent}
                disabled={!actionAvailability[intent].allowed}
                onClick={() => { setAction(intent); setFieldErrors((current) => ({ ...current, action: '' })) }}
                className={`rounded-card border p-3 text-xs font-semibold transition-all text-center disabled:cursor-not-allowed disabled:opacity-50 ${
                  action === intent
                    ? 'border-app-action bg-app-action/10 text-app-action ring-1 ring-app-action'
                    : 'border-app-border bg-app-surface text-app-muted hover:text-app-text'
                }`}
              >
                {intent === 'start' ? 'Start new session' : 'Switch session'}
              </button>
            ))}
          </div>
          {(!actionAvailability.start.allowed || !actionAvailability.switch.allowed) && (
            <p className="mt-1 text-[11px] text-app-muted">
              {!actionAvailability.start.allowed && actionAvailability.start.detail}
              {!actionAvailability.start.allowed && !actionAvailability.switch.allowed ? ' · ' : ''}
              {!actionAvailability.switch.allowed && actionAvailability.switch.detail}
            </p>
          )}
          {(fieldErrors.action || validationErrors.action || operationBlockedReason || feedbackModel?.inlineErrors.action?.length) && (
            <p className="mt-1 text-xs text-app-danger">{operationBlockedReason || validationErrors.action || fieldErrors.action || feedbackModel?.inlineErrors.action?.join(', ')}</p>
          )}
        </div>
        {/* 1. Market Mode */}
        <div>
          <span className="block text-xs font-semibold text-app-text mb-2">
            Market Mode
          </span>
          <div className="grid grid-cols-3 gap-2.5">
            {marketModes.map((id) => (
              <button
                key={id}
                type="button"
                aria-pressed={marketMode === id}
                onClick={() => setMarketMode(id)}
                className={`rounded-card border p-3 text-xs font-semibold transition-all text-center ${
                  marketMode === id
                    ? 'border-app-action bg-app-action/10 text-app-action ring-1 ring-app-action'
                    : 'border-app-border bg-app-surface text-app-muted hover:border-app-border/80 hover:text-app-text'
                }`}
              >
                {id === 'both' ? 'Both (Spot & Perp)' : id === 'spot' ? 'Spot' : 'Perpetual (Perp)'}
              </button>
            ))}
          </div>
          {(fieldErrors.marketMode || validationErrors.marketMode || feedbackModel?.inlineErrors.marketMode?.length) && (
            <p className="mt-1 text-xs text-app-danger">{validationErrors.marketMode || fieldErrors.marketMode || feedbackModel?.inlineErrors.marketMode?.join(', ')}</p>
          )}
        </div>

        {/* 2. Profile Selection */}
        <div>
          <span className="block text-xs font-semibold text-app-text mb-2">Profile Selection</span>
          <div className="grid grid-cols-2 gap-2.5">
            <button type="button" aria-pressed={selection === 'fixed'} onClick={() => setSelection('fixed')}
              className={`rounded-card border p-3 text-xs font-semibold ${selection === 'fixed' ? 'border-app-action bg-app-action/10 text-app-action ring-1 ring-app-action' : 'border-app-border bg-app-surface text-app-muted'}`}>
              Fixed Profile
            </button>
            <button type="button" aria-pressed={selection === 'random'} disabled={randomProfiles.length === 0} onClick={() => setSelection('random')}
              className={`rounded-card border p-3 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${selection === 'random' ? 'border-app-action bg-app-action/10 text-app-action ring-1 ring-app-action' : 'border-app-border bg-app-surface text-app-muted'}`}>
              Random Selection
            </button>
          </div>
          {selection === 'fixed' ? (
            <Select
              id="profile-select"
              label="Profile in catalogue"
              sizeVariant="md"
              value={profileId ?? ''}
              onChange={(e) => setProfileId(e.target.value || null)}
              error={validationErrors.profileId || fieldErrors.profileId || feedbackModel?.inlineErrors.profileId?.[0]}
              containerClassName="mt-3"
              options={[
                { value: '', label: 'Select profile' },
                ...profileEntries.map((profile) => ({ value: profile, label: profile })),
              ]}
            />
          ) : (
            <p className="mt-2 text-xs text-app-muted">Catalogue random pool: {randomProfiles.join(', ') || 'No profiles available'}</p>
          )}
          {(fieldErrors.selection || validationErrors.selection || feedbackModel?.inlineErrors.selection?.length) && (
            <p className="mt-1 text-xs text-app-danger">{validationErrors.selection || fieldErrors.selection || feedbackModel?.inlineErrors.selection?.join(', ')}</p>
          )}
        </div>

        {/* 3. Duration Mode */}
        <div>
          <span className="block text-xs font-semibold text-app-text mb-2">
            Duration Mode
          </span>
          <div className="grid grid-cols-2 gap-2.5">
            {durationModes.map((mode) => <button
              type="button"
              key={mode}
              aria-pressed={durationMode === mode}
              disabled={mode === 'until_stopped' && !catalogue?.allow_until_stopped}
              onClick={() => setDurationMode(mode)}
              className={`rounded-card border p-3 text-xs font-semibold transition-all text-center ${
                durationMode === mode
                  ? 'border-app-action bg-app-action/10 text-app-action ring-1 ring-app-action'
                  : 'border-app-border bg-app-surface text-app-muted hover:border-app-border/80 hover:text-app-text'
              }`}
            >
              {mode === 'timed' ? 'Timed' : 'Until manually stopped'}
            </button>)}
          </div>
          {(fieldErrors.durationMode || validationErrors.durationMode || feedbackModel?.inlineErrors.durationMode?.length) && (
            <p className="mt-1 text-xs text-app-danger">{validationErrors.durationMode || fieldErrors.durationMode || feedbackModel?.inlineErrors.durationMode?.join(', ')}</p>
          )}
        </div>

        {/* 4. Timed Input */}
        {durationMode === 'timed' && (
          <div>
            <div className="rounded-card border border-app-border p-3">
              <label className="flex items-center gap-2 text-xs font-semibold text-app-text">
                <input type="checkbox" checked={durationSeconds === null}
                  onChange={(event) => setDurationSeconds(event.target.checked ? null : defaultSeconds)} />
                Use default duration from YAML ({defaultSeconds} seconds)
              </label>
              {durationSeconds !== null && <Input id="duration-input" label="Duration (seconds)" type="number" min={minSeconds} max={maxSeconds} step={1}
                value={durationSeconds} onChange={(event) => setDurationSeconds(event.target.value === '' ? 0 : Number(event.target.value))}
                error={validationErrors.durationSeconds || validationErrors.duration_seconds || fieldErrors.durationSeconds || feedbackModel?.inlineErrors.durationSeconds?.[0]} />}
            </div>
          </div>
        )}

        {/* Read-Only Managed Settings Note */}
        <div className="rounded-card border border-app-border/60 bg-app-surface-muted/20 px-3.5 py-2.5 text-xs text-app-muted flex items-center justify-between gap-2">
          <span>Position budgets, spread skew, and drawdown limits are synchronized automatically from the catalogue YAML.</span>
          <Badge tone="neutral" className="text-[10px] shrink-0">Managed YAML</Badge>
        </div>

        {/* Feedback Details and Diagnostics (T-15) */}
        {(feedbackModel?.feedback || (feedbackModel?.globalFields && feedbackModel.globalFields.length > 0)) && (
          <FeedbackDetailsView
            feedback={feedbackModel.feedback}
            globalFields={feedbackModel.globalFields}
            onRetry={onRetry}
            onRefresh={onRefresh}
            onReview={onReview}
          />
        )}

        {/* Submit review */}
        <div className="pt-2">
          {(Object.keys(validationErrors).length > 0 || operationBlockedReason) && !fieldErrors.action && (
            <p role="alert" className="mb-2 text-xs text-app-danger">{operationBlockedReason || Object.values(validationErrors).join(' ')}</p>
          )}
          <Button
            type="submit"
            disabled={isLoading || isSubmitting || !actionAvailability[action].allowed || Boolean(operationBlockedReason)}
            className="w-full font-bold"
          >
            Continue: Review Configuration Diff
          </Button>
        </div>
      </form>
    </Surface>
  )
}

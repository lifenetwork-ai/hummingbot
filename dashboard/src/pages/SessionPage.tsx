import { useState } from 'react'
import { ArrowRight, Layers, SlidersHorizontal } from 'lucide-react'
import { notify } from '../core/feedback/notify'
import { buildConfigurationReview } from '../features/session-control/configuration-review'
import { ConfigurationReadOnlyView } from '../features/session/ConfigurationReadOnlyView'
import { FeedbackDetailsView } from '../features/session/FeedbackDetailsView'
import type { SessionReview } from '../features/session-control/session-control'
import { useSessionControl } from '../features/session-control/useSessionControl'
import { useSessionConfiguration } from '../features/history/useHistoryQueries'
import { useCommandReceipt } from '../core/api/hooks'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { CommandReceiptTracker } from '../features/session/CommandReceiptTracker'
import { SessionConfigForm, type SessionDraftConfig } from '../features/session/SessionConfigForm'
import { SessionDiffReview } from '../features/session/SessionDiffReview'
import { Button } from '../shared/components/Button'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export function SessionPage() {
  const context = useOperationalContext()
  const control = useSessionControl()
  const profilesQuery = control.profiles
  const currentSessionId = context.status.data?.session?.id
  const pinned = useSessionConfiguration(currentSessionId)
  const [step, setStep] = useState<'form' | 'review' | 'tracker'>('form')
  const [draftConfig, setDraftConfig] = useState<SessionDraftConfig | null>(null)
  const [reviewed, setReviewed] = useState<SessionReview | null>(null)
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({})
  const [submitError, setSubmitError] = useState<string | null>(null)
  const receipt = useCommandReceipt(control.commandReceipt?.reference.id)
  const currentControlVersion = context.controlVersion
  const reportError = (error: unknown) => error instanceof Error ? error.message : 'Could not verify the outcome of the operation.'
  const handleReview = (action: 'start' | 'switch', draft: SessionDraftConfig) => {
    if (draft.marketMode === null) { setValidationErrors({ marketMode: 'Select a market from the catalogue.' }); return }
    const checked = control.review(action, { ...draft, marketMode: draft.marketMode })
    setValidationErrors(checked.errors)
    if (!checked.review) return
    control.clearReviewedFeedback()
    setReviewed(checked.review)
    setDraftConfig(draft)
    setSubmitError(null)
    setStep('review')
  }
  const handleConfirmSubmit = async () => {
    if (!reviewed) return
    setSubmitError(null)
    notify.loading('Applying session configuration...', { id: 'session-submit' })
    try {
      await control.submitReviewed(reviewed)
      setStep('tracker')
      notify.info('Configuration receipt received — verification not yet complete', {
        id: 'session-submit',
        description: 'Track the receipt and watch status/actual orders to verify the outcome.',
      })
    }
    catch (error) {
      const msg = reportError(error)
      setSubmitError(msg)
      notify.dismiss('session-submit')
    }
  }
  const retry = async (stop = false) => {
    notify.info('Retrying the command with the same Idempotency Key...', { id: 'session-retry' })
    try {
      if (stop) await control.retryStop()
      else { await control.retryPending(); setStep('tracker') }
      notify.info('Retry receipt received — continuing to track', { id: 'session-retry' })
    }
    catch (error) {
      const msg = reportError(error)
      setSubmitError(msg)
      notify.dismiss('session-retry')
    }
  }
  const pinnedRequest = pinned.data?.request
  const configurationReview = buildConfigurationReview(profilesQuery.data, pinned.data)
  const refreshReview = () => { void control.refreshStatus(); void control.refreshProfiles(); if (currentSessionId) void pinned.refetch(); setStep('form') }
  const [pageTab, setPageTab] = useState<'control' | 'spec'>('control')

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] px-4 pb-16 pt-4 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        {/* Page Header */}
        <div className="mb-4 flex flex-wrap items-start justify-between gap-4">
          <div>
            <SectionLabel tone="brand">Configuration & Control</SectionLabel>
            <h1 className="mt-1 font-heavy text-2xl font-bold tracking-tight text-app-text md:text-3xl flex items-center gap-2.5">
              <SlidersHorizontal size={26} className="text-app-action" />
              <span>Run Session Setup</span>
            </h1>
            <p className="mt-2 text-sm text-app-muted">
              Manage the market-making strategy, switch Profiles, and adjust the MM session duration.
            </p>
          </div>
        </div>

        {/* Tab Navigation: separates control actions from detailed configuration lookup */}
        <div className="mb-6 border-b border-app-border">
          <nav className="flex space-x-2 overflow-x-auto pb-1" aria-label="Session operation tabs">
            <button
              type="button"
              onClick={() => setPageTab('control')}
              aria-current={pageTab === 'control' ? 'page' : undefined}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors whitespace-nowrap ${
                pageTab === 'control'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <SlidersHorizontal size={15} />
              <span>Configure & Launch Session</span>
            </button>
            <button
              type="button"
              onClick={() => setPageTab('spec')}
              aria-current={pageTab === 'spec' ? 'page' : undefined}
              className={`inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-xs font-bold transition-colors whitespace-nowrap ${
                pageTab === 'spec'
                  ? 'border-app-action text-app-action'
                  : 'border-transparent text-app-muted hover:border-app-border hover:text-app-text'
              }`}
            >
              <Layers size={15} />
              <span>Configuration Details (Catalogue Spec & Pinned)</span>
            </button>
          </nav>
        </div>

        {/* Unified Operational Notices */}
        {(submitError || profilesQuery.isError || control.pendingRetry || control.stopPendingRetry || control.stopReceipt || (step === 'form' && (context.status.phase !== 'ready' || !profilesQuery.isSuccess))) && (
          <div className="mb-5 space-y-2 rounded-card border border-app-border/80 bg-app-surface-muted/30 p-3.5 text-xs">
            {submitError && <p role="alert" className="text-sm font-medium text-app-danger">{submitError}</p>}
            {profilesQuery.isError && <p role="alert" className="text-sm text-app-danger">Failed to load the catalogue. <button type="button" className="underline font-bold" onClick={() => { void control.refreshProfiles() }}>Try again</button></p>}
            {control.pendingRetry && <div className="text-sm text-app-warning flex items-center justify-between gap-2"><span>Command outcome unknown.</span> <Button variant="secondary" onClick={() => { void retry() }}>Retry with same body/key</Button></div>}
            {control.stopPendingRetry && <div className="text-sm text-app-warning flex items-center justify-between gap-2"><span>Stop outcome unknown.</span> <Button variant="secondary" onClick={() => { void retry(true) }}>Retry stop with same body/key</Button></div>}
            {control.stopReceipt && <p role="status" className="text-xs text-app-muted">Stop receipt {control.stopReceipt.reference.id}: {control.stopReceipt.status ?? 'Unknown'} · Positions remain open; check actual orders.</p>}
            {step === 'form' && (context.status.phase !== 'ready' || !profilesQuery.isSuccess) && <p role="status" className="text-sm text-app-muted">Waiting for verified status and catalogue. <button type="button" className="underline font-bold ml-1" onClick={() => { void control.refreshStatus(); void control.refreshProfiles() }}>Refresh data sources</button></p>}
          </div>
        )}

        {control.commandReceipt && step !== 'tracker' && (
          <div className="mb-5">
            <CommandReceiptTracker receipt={receipt.data} isLoading={receipt.isLoading} error={receipt.error?.message} />
          </div>
        )}

        {/* Tab 1: Session control form */}
        {pageTab === 'control' && (
          <>
            {/* Content steps */}
            {step === 'form' && context.status.phase === 'ready' && profilesQuery.isSuccess && (
              <>
                <SessionConfigForm
                  initialConfig={draftConfig ?? undefined}
                  initialAction={context.actions.start.allowed ? 'start' : 'switch'}
                  actionAvailability={{ start: context.actions.start, switch: context.actions.switch }}
                  activeSessionId={currentSessionId}
                  validationErrors={validationErrors}
                  feedbackModel={control.formFeedback}
                  onRetry={control.pendingRetry ? () => { void retry() } : undefined}
                  onRefresh={refreshReview}
                  onReview={refreshReview}
                  operationBlockedReason={control.commandBlocksNew ? 'Tracking an unfinished operation.' : null}
                  profilesData={profilesQuery.data}
                  isLoading={profilesQuery.isLoading}
                  onSubmitReview={handleReview}
                />
                <div className="mt-4 flex items-center justify-between rounded-card border border-app-border/60 bg-app-surface-muted/20 px-4 py-2.5 text-xs text-app-muted">
                  <span>Want to inspect the full YAML parameters of the pinned snapshot and sample catalogue?</span>
                  <button
                    type="button"
                    onClick={() => setPageTab('spec')}
                    className="font-semibold text-app-action hover:underline inline-flex items-center gap-1 shrink-0"
                  >
                    <span>View configuration details</span>
                    <ArrowRight size={13} />
                  </button>
                </div>
              </>
            )}

            {step === 'review' && draftConfig && (
              <SessionDiffReview
                configurationReview={configurationReview}
                currentConfig={pinnedRequest ? {
                  marketMode: pinnedRequest.market_mode,
                  selection: pinnedRequest.selection,
                  profileId: pinnedRequest.profile_id,
                  profileIds: pinnedRequest.profile_ids ?? pinned.data?.catalogue.random.profile_ids,
                  durationMode: pinnedRequest.duration_mode,
                  durationSeconds: pinnedRequest.duration_seconds,
                  sessionId: pinned.data?.session_id,
                } : undefined}
                currentConfigurationAvailable={!currentSessionId || pinned.isSuccess}
                proposedProfileIds={profilesQuery.data?.catalogue.random.profile_ids}
                proposedConfig={draftConfig}
                action={reviewed?.action}
                controlVersion={reviewed?.reviewedAtVersion ?? currentControlVersion}
                isSubmitting={control.submitting}
                submitAllowed={Boolean(reviewed && context.status.phase === 'ready' && profilesQuery.isSuccess && context.controlVersion === reviewed.reviewedAtVersion && profilesQuery.data.catalogue_hash === reviewed.catalogueHash && context.actions[reviewed.action].allowed && !control.commandBlocksNew)}
                blockedDetail={reviewed && !context.actions[reviewed.action].allowed ? context.actions[reviewed.action].detail : undefined}
                onBack={() => setStep('form')}
                onConfirmSubmit={() => { void handleConfirmSubmit() }}
              />
            )}

            {step === 'tracker' && (
              <CommandReceiptTracker
                receipt={receipt.data}
                isLoading={receipt.isLoading || control.submitting}
                error={submitError ?? receipt.error?.message}
                onReset={() => {
                  setStep('form')
                }}
              />
            )}

            {step !== 'form' && <FeedbackDetailsView feedback={control.formFeedback.feedback} globalFields={control.formFeedback.globalFields} onRetry={control.pendingRetry ? () => { void retry() } : undefined} onRefresh={refreshReview} onReview={refreshReview} />}
            {control.stopFormFeedback.feedback && <FeedbackDetailsView title="Stop Feedback" feedback={control.stopFormFeedback.feedback} globalFields={control.stopFormFeedback.globalFields} onRetry={control.stopPendingRetry ? () => { void retry(true) } : undefined} onRefresh={() => { void control.refreshStatus() }} />}
          </>
        )}

        {/* Tab 2: Full configuration lookup */}
        {pageTab === 'spec' && profilesQuery.isSuccess && (
          <div className="space-y-4">
            <ConfigurationReadOnlyView model={configurationReview} />
          </div>
        )}
      </div>
    </MotionPage>
  )
}

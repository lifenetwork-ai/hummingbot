import { Check, RotateCcw, Tag } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { Input } from '../../shared/components/Input'

export interface RecoveryScopeSelectorProps {
  currentIds: readonly string[]
  validationError?: string | null
  selectionLocked?: boolean
  isPending?: boolean
  suggestedOrderId?: string | null
  suggestionFresh?: boolean
  onScopeChange?: (ids: string[]) => void
  onClearScope?: () => void
  disabled?: boolean
  scopeMode?: 'generic' | 'scoped'
  onScopeModeChange?: (mode: 'generic' | 'scoped') => void
}

export function RecoveryScopeSelector({
  currentIds,
  validationError,
  selectionLocked = false,
  isPending = false,
  suggestedOrderId,
  suggestionFresh = false,
  onScopeChange,
  onClearScope,
  disabled = false,
  scopeMode = currentIds.length > 0 ? 'scoped' : 'generic',
  onScopeModeChange,
}: RecoveryScopeSelectorProps) {
  const [draftInput, setDraftInput] = useState(() => currentIds.join(', '))
  const [localError, setLocalError] = useState<string | null>(null)

  useEffect(() => {
    setDraftInput(currentIds.join(', '))
  }, [currentIds])

  const isScopedActive = scopeMode === 'scoped' || currentIds.length > 0
  const isApplyDisabled = disabled || isPending || (selectionLocked && currentIds.length > 0)
  const isClearReviewDisabled = disabled || isPending

  const applyLockReason = isPending
    ? 'Wait for the current recovery operation to finish.'
    : selectionLocked && currentIds.length > 0
      ? 'Clear the current recovery review before selecting different order IDs.'
      : undefined

  const clearLockReason = isPending
    ? 'Wait for the current recovery operation to finish before clearing review.'
    : undefined

  const handleModeSwitch = (mode: 'generic' | 'scoped') => {
    if (disabled || isPending) return
    onScopeModeChange?.(mode)
    if (mode === 'generic') {
      setDraftInput('')
      setLocalError(null)
      onScopeChange?.([])
      onClearScope?.()
    }
  }

  const handleApplyScope = () => {
    setLocalError(null)
    const trimmed = draftInput.trim()
    if (!trimmed) {
      setLocalError('Enter 1 or 2–8 client order IDs.')
      return
    }
    const ids = trimmed.split(/[\s,]+/).filter(Boolean)
    try {
      onScopeChange?.(ids)
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Invalid order IDs')
    }
  }

  const handleClearReview = () => {
    setDraftInput('')
    setLocalError(null)
    onScopeChange?.([])
    onClearScope?.()
  }

  const displayError = localError || validationError

  return (
    <div className="mt-5 rounded-card border border-app-border bg-app-surface-muted/30 p-4 text-xs text-app-text">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-app-border/60 pb-3">
        <div>
          <h4 className="flex items-center gap-1.5 font-bold uppercase tracking-wider text-app-text">
            <Tag size={14} className="text-app-action" />
            Recovery Scope
          </h4>
          <p className="mt-0.5 text-[11px] text-app-muted">
            Select generic system checkpoint recovery or scoped uncertain order review.
          </p>
        </div>
        <div className="flex rounded-button border border-app-border bg-app-surface p-0.5" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={!isScopedActive}
            disabled={disabled || isPending}
            onClick={() => handleModeSwitch('generic')}
            className={`rounded-button px-3 py-1 text-xs font-semibold transition-colors ${
              !isScopedActive
                ? 'bg-app-action text-white'
                : 'text-app-muted hover:text-app-text disabled:opacity-50'
            }`}
          >
            Generic (Checkpoint)
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isScopedActive}
            disabled={disabled || isPending}
            onClick={() => handleModeSwitch('scoped')}
            className={`rounded-button px-3 py-1 text-xs font-semibold transition-colors ${
              isScopedActive
                ? 'bg-app-action text-white'
                : 'text-app-muted hover:text-app-text disabled:opacity-50'
            }`}
          >
            Uncertain Orders
          </button>
        </div>
      </div>

      {isScopedActive && (
        <div className="mt-3.5 space-y-3">
          <div className="grid gap-2">
            <Input
              id="uncertain-order-ids-input"
              label="Client Order IDs"
              placeholder="e.g. Spot001, Spot002"
              value={draftInput}
              onChange={(e) => {
                setDraftInput(e.target.value)
                setLocalError(null)
              }}
              disabled={isApplyDisabled}
              sizeVariant="sm"
              hint="Enter 1 or 2–8 alphanumeric client order IDs (1–32 characters, distinct)."
              error={displayError || undefined}
            />

            {suggestedOrderId && suggestionFresh && !currentIds.includes(suggestedOrderId) && (
              <div className="flex items-center gap-2 text-[11px]">
                <span className="text-app-muted">Suggested from runtime:</span>
                <button
                  type="button"
                  onClick={() => {
                    setDraftInput(suggestedOrderId)
                    setLocalError(null)
                  }}
                  disabled={isApplyDisabled}
                  className="font-mono text-app-action hover:underline disabled:opacity-50"
                >
                  {suggestedOrderId}
                </button>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                onClick={handleApplyScope}
                disabled={isApplyDisabled}
                title={applyLockReason}
                className="h-7 px-3 text-xs font-semibold gap-1"
              >
                <Check size={12} />
                <span>Apply order scope</span>
              </Button>

              {currentIds.length > 0 && (
                <Button
                  variant="secondary"
                  onClick={handleClearReview}
                  disabled={isClearReviewDisabled}
                  title={clearLockReason}
                  className="h-7 px-3 text-xs gap-1"
                >
                  <RotateCcw size={12} />
                  <span>Xóa review</span>
                </Button>
              )}
            </div>

            {applyLockReason && (
              <span className="text-[11px] text-app-warning" role="note">
                {applyLockReason}
              </span>
            )}
          </div>

          {currentIds.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 pt-2 border-t border-app-border/40">
              <span className="text-[11px] font-semibold text-app-muted">Selected orders ({currentIds.length}):</span>
              {currentIds.map((id) => (
                <Badge key={id} tone="brand" className="font-mono text-[11px]">
                  {id}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

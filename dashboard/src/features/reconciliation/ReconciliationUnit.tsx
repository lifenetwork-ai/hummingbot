import { useState } from 'react'
import { useAdminQuery } from '../../core/api/hooks'
import { notify } from '../../core/feedback/notify'
import { Button } from '../../shared/components/Button'
import { ReconciliationPanel } from '../activity/ReconciliationPanel'
import { useReconciliationOperation, useReconciliationReadLease } from './useReconciliationOperation'

const checkBody = { lookback_seconds: 3600, market: 'all' as const, max_orders: 100, max_pages: 5, pair: null }

export function ReconciliationUnit({ active }: { active: boolean }) {
  useReconciliationReadLease(active)
  const reconciliation = useReconciliationOperation()
  const [checkError, setCheckError] = useState<string | null>(null)
  const receiptId = reconciliation.receipt?.reference.id
  const exactCheck = useAdminQuery('getReconciliationById', receiptId ? { path: { check_id: receiptId } } : undefined, { enabled: active && Boolean(receiptId), poll: false })

  const requestCheck = async (retry = false) => {
    setCheckError(null)
    notify.loading('Submitting reconciliation request...', { id: 'recon-check' })
    try {
      if (retry) await reconciliation.retryPending()
      else await reconciliation.check(checkBody)
      notify.success('Reconciliation request accepted', {
        id: 'recon-check',
        description: 'Track the receipt to verify progress; accepted does not yet mean the data has matched.',
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Reconciliation result unknown.'
      setCheckError(message)
      notify.dismiss('recon-check')
    }
  }

  if (!active) return null
  return (
    <section aria-label="Exchange reconciliation">
      {(checkError || reconciliation.latest.error) && <p role="alert" className="mb-3 text-sm text-app-danger">{checkError ?? reconciliation.latest.error?.message}</p>}
      {reconciliation.pendingRetry && <Button variant="secondary" onClick={() => { void requestCheck(true) }}>Retry check with same body/key</Button>}
      {reconciliation.feedback?.requestId && <p className="text-xs text-app-muted">Request ID: {reconciliation.feedback.requestId}</p>}
      <ReconciliationPanel
        reconciliation={reconciliation.latest.data}
        receipt={exactCheck.data}
        isChecking={reconciliation.submitting || reconciliation.receipt?.status === 'accepted' || reconciliation.receipt?.status === 'applying'}
        canRequest={reconciliation.access.allowed && !reconciliation.pendingRetry}
        onRequestCheck={() => { void requestCheck() }}
      />
    </section>
  )
}

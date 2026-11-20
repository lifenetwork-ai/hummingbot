import { useState } from 'react'
import { notify } from '../../core/feedback/notify'
import { FeedbackDetailsView } from '../session/FeedbackDetailsView'
import { AdminRecoveryView } from '../admin/AdminRecoveryView'
import { useRecoveryOperationContext } from './RecoveryOperationContext'

export function RecoveryUnit() {
  const recovery = useRecoveryOperationContext()
  const [checkError, setCheckError] = useState<string>()
  const [resetError, setResetError] = useState<string>()

  const runCheck = async (retry = false) => {
    setCheckError(undefined)
    const id = retry ? 'admin-recovery-retry' : 'admin-recovery'
    notify.loading(retry ? 'Retrying recovery check...' : 'Checking system recovery...', { id })
    try {
      await (retry ? recovery.retryCheck() : recovery.check())
      notify.info(retry ? 'Retry check receipt received — continuing to monitor' : 'Check receipt received — awaiting check result', { id })
    } catch (error) {
      setCheckError(error instanceof Error ? error.message : 'Recovery check failed.')
      notify.dismiss(id)
    }
  }

  const runReset = async (retry = false) => {
    setResetError(undefined)
    const id = retry ? 'admin-recovery-reset-retry' : 'admin-recovery-reset'
    notify.loading(retry ? 'Retrying recovery state reset...' : 'Resetting recovery state...', { id })
    try {
      await (retry ? recovery.retryReset() : recovery.confirmReset())
      notify.info(retry ? 'Retry Reset receipt received — continuing to monitor' : 'Reset receipt received — recovered not yet verified', { id })
    } catch (error) {
      setResetError(error instanceof Error ? error.message : 'Recovery reset failed.')
      notify.dismiss(id)
      throw error
    }
  }

  const model = { ...recovery.viewModel, checkError, resetError }
  return <>
    <AdminRecoveryView model={model}
      onCheckRecovery={() => runCheck()}
      onRetryCheck={() => runCheck(true)}
      onResetRecovery={() => runReset()}
      onRetryReset={() => runReset(true)}
      onRefresh={recovery.refresh} />
    <FeedbackDetailsView title="Recovery check feedback" feedback={recovery.feedback.check.feedback}
      globalFields={recovery.feedback.check.globalFields}
      onRetry={recovery.canRetryCheck ? () => { void runCheck(true) } : undefined}
      onRefresh={() => { void recovery.refresh() }} onReview={() => { void recovery.refresh() }} />
    <FeedbackDetailsView title="Recovery reset feedback" feedback={recovery.feedback.reset.feedback}
      globalFields={recovery.feedback.reset.globalFields}
      onRetry={recovery.canRetryReset ? () => { void runReset(true) } : undefined}
      onRefresh={() => { void recovery.refresh() }} onReview={() => { void recovery.refresh() }} />
  </>
}

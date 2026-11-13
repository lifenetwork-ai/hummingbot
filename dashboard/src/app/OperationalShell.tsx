import { useState, useEffect } from 'react'
import { Outlet } from '@tanstack/react-router'
import { Power } from 'lucide-react'
import { getAppConfig } from '../core/config/env'
import { notify } from '../core/feedback/notify'
import { AssetRibbon } from '../features/operational-context/AssetRibbon'
import { ContextBar } from '../features/operational-context/ContextBar'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { useTrackedReceipts } from '../core/api/useTrackedReceipts'
import { useReceiptInspection } from '../core/api/hooks'
import { useSessionControl } from '../features/session-control/useSessionControl'
import type { ReceiptReference } from '../core/api/receipt-tracking'
import { StopConfirmationDialog } from '../features/session/StopConfirmationDialog'

/** Persistent operational content shared by every authenticated child route. */
export function OperationalShell() {
  const context = useOperationalContext()
  const trackedReceipts = useTrackedReceipts()
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptReference | null>(null)
  const receiptInspection = useReceiptInspection(selectedReceipt)
  const [stopDialogOpen, setStopDialogOpen] = useState(false)
  const stopControl = useSessionControl({ includeProfiles: false, trackCommands: false })
  const [stopError, setStopError] = useState<string | null>(null)

  useEffect(() => {
    if (selectedReceipt && !trackedReceipts.some((r) => r.kind === selectedReceipt.kind && r.id === selectedReceipt.id)) {
      setSelectedReceipt(null)
    }
  }, [trackedReceipts, selectedReceipt])

  // Global listener for the Stop Orders button in header
  useEffect(() => {
    const handleOpenStop = () => setStopDialogOpen(true)
    window.addEventListener('open-stop-dialog', handleOpenStop)
    return () => window.removeEventListener('open-stop-dialog', handleOpenStop)
  }, [])

  const handleStopConfirm = async () => {
    setStopError(null)
    notify.loading('Submitting stop command...', { id: 'shell-stop' })
    try {
      await stopControl.stop()
      notify.success('Stop command submitted successfully', {
        id: 'shell-stop',
        description: 'Positions remain intact; bot is cancelling open orders.',
      })
    }
    catch (error) {
      const msg = error instanceof Error ? error.message : 'Stop outcome unknown.'
      setStopError(msg)
      notify.error('Stop command incomplete', {
        id: 'shell-stop',
        description: msg,
      })
      throw error
    }
  }

  return (
    <>
      <AssetRibbon />
      {stopControl.stopPendingRetry && <div className="mx-auto w-full max-w-[1536px] px-4 py-2 text-xs text-app-warning" role="status">Stop outcome unknown. <button type="button" disabled={stopControl.stopping} onClick={() => { void stopControl.retryStop().catch((error: unknown) => setStopError(error instanceof Error ? error.message : 'Retry Stop failed.')) }}>Retry Stop with same body/key</button>{stopError}</div>}
      {stopControl.stopReceipt && <p role="status" className="mx-auto w-full max-w-[1536px] px-4 text-xs text-app-muted">Stop {stopControl.stopReceipt.reference.id}: {stopControl.stopReceipt.status ?? 'Unknown'} · Positions intact.</p>}
      <Outlet />

      {/* Single global emergency stop — floating above the ContextBar, visible from any page.
          Solid red circle with a power-off icon, like the stop/shutdown button on everyday devices. */}
      <button
        type="button"
        onClick={() => setStopDialogOpen(true)}
        aria-label="Emergency stop"
        disabled={!context.actions.stop.allowed || stopControl.stopping}
        title={context.actions.stop.detail}
        className="fixed bottom-14 right-4 z-40 grid h-14 w-14 place-items-center rounded-full bg-app-danger text-white shadow-lg transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-danger focus-visible:ring-offset-2 active:scale-95 disabled:pointer-events-none disabled:opacity-40 sm:right-6"
      >
        <Power size={26} strokeWidth={2.5} className="shrink-0" />
      </button>

      {/* Operational Context persistent footer bar */}
      <ContextBar
        context={context}
        apiBaseUrl={getAppConfig().adminApiBaseUrl}
        trackedReceipts={trackedReceipts}
        selectedReceipt={selectedReceipt}
        receiptInspection={receiptInspection}
        onSelectReceipt={setSelectedReceipt}
      />

      {/* Global emergency Stop dialog accessible from anywhere in workspace */}
      <StopConfirmationDialog
        open={stopDialogOpen}
        onOpenChange={setStopDialogOpen}
        context={context}
        onConfirm={handleStopConfirm}
        isSubmitting={stopControl.stopping}
        error={stopError}
      />
    </>
  )
}

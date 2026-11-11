import { useState, useEffect } from 'react'
import { Outlet } from '@tanstack/react-router'
import { getAppConfig } from '../core/config/env'
import { ContextBar } from '../features/operational-context/ContextBar'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { useTrackedReceipts } from '../core/api/useTrackedReceipts'
import { useReceiptInspection } from '../core/api/hooks'
import type { ReceiptReference } from '../core/api/receipt-tracking'

/** Persistent operational content shared by every authenticated child route. */
export function OperationalShell() {
  const context = useOperationalContext()
  const trackedReceipts = useTrackedReceipts()
  const [selectedReceipt, setSelectedReceipt] = useState<ReceiptReference | null>(null)
  const receiptInspection = useReceiptInspection(selectedReceipt)

  useEffect(() => {
    if (selectedReceipt && !trackedReceipts.some((r) => r.kind === selectedReceipt.kind && r.id === selectedReceipt.id)) {
      setSelectedReceipt(null)
    }
  }, [trackedReceipts, selectedReceipt])

  return (
    <>
      <ContextBar
        context={context}
        apiBaseUrl={getAppConfig().adminApiBaseUrl}
        trackedReceipts={trackedReceipts}
        selectedReceipt={selectedReceipt}
        receiptInspection={receiptInspection}
        onSelectReceipt={setSelectedReceipt}
      />
      <Outlet />
    </>
  )
}

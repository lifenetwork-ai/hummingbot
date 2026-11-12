import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CommandInspectionModal } from './CommandInspectionModal'
import type { ReceiptReference } from '../../core/api/receipt-tracking'

describe('CommandInspectionModal', () => {
  const reference: ReceiptReference = {
    kind: 'command',
    id: 'cmd-inspect-123456',
  }

  it('returns null when reference is null', () => {
    const { container } = render(
      <CommandInspectionModal reference={null} onClose={vi.fn()} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders receipt details, status, outcome and disclaimer', () => {
    const onClose = vi.fn()
    const onAudit = vi.fn()
    render(
      <CommandInspectionModal
        reference={reference}
        inspection={{
          phase: 'ready',
          status: 'succeeded',
          outcome: { session_id: 'sess-abc', result: 'ok' },
        }}
        onClose={onClose}
        onAuditForOperation={onAudit}
      />,
    )

    expect(screen.getByText('Operation Receipt Inspection')).toBeInTheDocument()
    expect(screen.getByText(/command Receipt/i)).toBeInTheDocument()
    expect(screen.getByText('cmd-inspect-123456')).toBeInTheDocument()
    expect(screen.getByText('succeeded')).toBeInTheDocument()
    expect(screen.getByText(/"session_id": "sess-abc"/)).toBeInTheDocument()
    expect(
      screen.getByText(/A succeeded receipt status only confirms backend acknowledgment/i),
    ).toBeInTheDocument()

    const auditBtn = screen.getByRole('button', { name: /Find in Audit Log/i })
    fireEvent.click(auditBtn)
    expect(onAudit).toHaveBeenCalledWith('cmd-inspect-123456')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('renders loading state when phase is loading', () => {
    render(
      <CommandInspectionModal
        reference={reference}
        inspection={{ phase: 'loading' }}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByText('Loading…')).toBeInTheDocument()
  })

  it('renders error alert when inspection encounters error', () => {
    render(
      <CommandInspectionModal
        reference={reference}
        inspection={{
          phase: 'error',
          error: new Error('Receipt not found on server'),
        }}
        onClose={vi.fn()}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Receipt not found on server')
  })
})

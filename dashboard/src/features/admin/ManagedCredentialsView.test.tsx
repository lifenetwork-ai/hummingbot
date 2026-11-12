import { render, screen, fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ManagedCredentialsView } from './ManagedCredentialsView'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'

describe('ManagedCredentialsView', () => {
  const sampleSlot = {
    exchange: 'okx',
    connector: 'okx',
    descriptor: { exchange: 'okx', connector: 'okx', site: 'OKX Spot', environment: 'live' as const },
    fields: [
      { name: 'api_key', label: 'API Key', secret: false, required: true },
      { name: 'secret', label: 'Secret Key', secret: true, required: true },
    ],
    slot: { configured: true, status: 'verified', label: 'OKX Live', changed_at: '2026-11-10T00:00:00Z', changed_by: 'admin', observed_at: '2026-11-10T00:00:00Z', detail: null },
    source: 'postgres_kms' as const,
    connectionState: 'connected' as const,
    decisions: {
      set: { allowed: true, detail: 'Ready to set', reasonCode: null },
      disconnect: { allowed: true, detail: 'Ready to disconnect', reasonCode: null },
      retry: { allowed: false, detail: 'No cleanup', reasonCode: null },
      repair: { allowed: false, detail: 'No cleanup', reasonCode: null },
    },
    cleanup: null,
    disconnectedProof: false,
    stopAcknowledged: true,
  } as unknown as Parameters<typeof ManagedCredentialsView>[0]['slots'][number]

  it('renders credential slots with advertised input fields and connection state', () => {
    render(
      <ManagedCredentialsView
        slots={[sampleSlot]}
        locks={[]}
        canManage={true}
        isLoading={false}
        isSubmitting={false}
        onRefresh={vi.fn()}
        onSetCredentials={vi.fn()}
        onRetryCleanup={vi.fn()}
        onRepairCleanup={vi.fn()}
        onReviewUnknown={vi.fn()}
        onOpenDisconnect={vi.fn()}
      />,
    )

    expect(screen.getByText('okx · okx')).toBeInTheDocument()
    expect(screen.getByText('connected')).toBeInTheDocument()
    expect(screen.getByText('postgres_kms')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Disconnect/ })).toBeInTheDocument()
  })

  it('renders inline error on credential field from feedbackModel when form is open', () => {
    const feedbackModel: FormFeedbackModel = {
      feedback: {
        surface: 'inline',
        code: 'invalid_credentials',
        requestId: 'req-cred-999',
        fields: [],
        retry: 'fix_request',
        retryAt: null,
        outcomeUnknown: false,
      },
      inlineErrors: {
        api_key: ['invalid_api_key_format'],
      },
      globalFields: [],
    }

    render(
      <ManagedCredentialsView
        slots={[sampleSlot]}
        locks={[]}
        canManage={true}
        isLoading={false}
        isSubmitting={false}
        onRefresh={vi.fn()}
        onSetCredentials={vi.fn()}
        onRetryCleanup={vi.fn()}
        onRepairCleanup={vi.fn()}
        onReviewUnknown={vi.fn()}
        onOpenDisconnect={vi.fn()}
        feedbackModel={feedbackModel}
      />,
    )

    // Open the set credentials form
    const openBtn = screen.getByRole('button', { name: /Set okx credentials/ })
    fireEvent.click(openBtn)

    // Inline error on advertised field
    expect(screen.getByText('invalid_api_key_format')).toBeInTheDocument()

    // Diagnostics feedback
    expect(screen.getByText('Code: invalid_credentials')).toBeInTheDocument()
  })
})

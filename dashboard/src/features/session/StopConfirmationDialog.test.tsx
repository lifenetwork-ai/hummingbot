import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { components } from '../../core/api/schema'
import { StopConfirmationDialog } from './StopConfirmationDialog'

describe('StopConfirmationDialog', () => {
  it('renders core message and positions remain open guarantee', () => {
    render(
      <StopConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirm={vi.fn()}
        context={{
          identity: null,
          botId: 'test-bot',
          controlVersion: 2,
          processEnvironment: 'demo',
          ownerEnvironment: 'demo',
          environmentMismatch: false,
          markets: [],
          observation: null,
          observationState: 'fresh',
          accounts: [],
          actions: {
            start: { allowed: false, reasonCode: null, detail: '' },
            switch: { allowed: false, reasonCode: null, detail: '' },
            stop: { allowed: true, reasonCode: null, detail: 'Allowed' },
          },
          status: { phase: 'ready', error: null, refreshing: false },
          balances: { phase: 'ready', error: null, refreshing: false },
        }}
      />,
    )

    expect(screen.getByText(/Stop market making; positions remain open/i)).toBeInTheDocument()
    expect(screen.getByText('Stop MM; positions remain open')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm stop bot now' })).toBeEnabled()
  })

  it('disables confirmation button and shows reason when stop is not allowed', () => {
    render(
      <StopConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        context={{
          identity: null,
          botId: 'test-bot',
          controlVersion: 2,
          processEnvironment: 'demo',
          ownerEnvironment: 'demo',
          environmentMismatch: false,
          markets: [],
          observation: null,
          observationState: 'fresh',
          accounts: [],
          actions: {
            start: { allowed: false, reasonCode: null, detail: '' },
            switch: { allowed: false, reasonCode: null, detail: '' },
            stop: { allowed: false, reasonCode: 'session_inactive', detail: 'Session inactive' },
          },
          status: { phase: 'ready', error: null, refreshing: false },
          balances: { phase: 'ready', error: null, refreshing: false },
        }}
      />,
    )

    expect(screen.getByText(/Session inactive/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Confirm stop bot now' })).toBeDisabled()
  })

  it('calls onConfirm when user clicks confirm button', () => {
    const onConfirm = vi.fn()
    render(
      <StopConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        onConfirm={onConfirm}
        context={{
          identity: null,
          botId: 'test-bot',
          controlVersion: 2,
          processEnvironment: 'demo',
          ownerEnvironment: 'demo',
          environmentMismatch: false,
          markets: [],
          observation: null,
          observationState: 'fresh',
          accounts: [],
          actions: {
            start: { allowed: false, reasonCode: null, detail: '' },
            switch: { allowed: false, reasonCode: null, detail: '' },
            stop: { allowed: true, reasonCode: null, detail: 'Allowed' },
          },
          status: { phase: 'ready', error: null, refreshing: false },
          balances: { phase: 'ready', error: null, refreshing: false },
        }}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm stop bot now' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('renders session ID with copy button and hover tooltip when session ID is available', () => {
    render(
      <StopConfirmationDialog
        open={true}
        onOpenChange={vi.fn()}
        context={{
          identity: null,
          botId: 'test-bot',
          controlVersion: 2,
          processEnvironment: 'demo',
          ownerEnvironment: 'demo',
          environmentMismatch: false,
          markets: [],
          observation: null,
          observationState: 'fresh',
          accounts: [],
          actions: {
            start: { allowed: false, reasonCode: null, detail: '' },
            switch: { allowed: false, reasonCode: null, detail: '' },
            stop: { allowed: true, reasonCode: null, detail: '' },
          },
          status: {
            phase: 'ready',
            error: null,
            refreshing: false,
            data: {
              observed_session: { id: 'sess-target-123456789' },
            } as unknown as components['schemas']['StatusResponseOutput'],
          },
          balances: { phase: 'ready', error: null, refreshing: false },
        }}
      />,
    )

    expect(screen.getByText('sess-target-123456789')).toBeInTheDocument()
    expect(screen.getByTitle('sess-target-123456789')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Copy session ID/ })).toBeInTheDocument()
  })
})

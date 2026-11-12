import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PinnedSessionDetailView } from './PinnedSessionDetailView'
import type { components } from '../../core/api/schema'

describe('PinnedSessionDetailView', () => {
  const sampleSessionState = {
    id: 'session-1234567890abcdef',
    profile_id: 'pure_market_making',
    status: 'running',
  } as unknown as components['schemas']['SessionStateOutput']

  const samplePinnedConfig = {
    session_id: 'session-1234567890abcdef',
    profile_id: 'pure_market_making',
    config_hash: 'hash-config-9999',
    catalogue_hash: 'hash-catalogue-8888',
    profile_context: 'at_creation',
    source: 'session_created_event',
    catalogue: {
      profile_id: 'pure_market_making',
      scenario: 'market_making',
      spread_multiplier: '1.0',
      order_refresh_tolerance_pct: '0.05',
      order_amount_multiplier: '1.0',
      inventory_target_base_pct: '50.0',
      inventory_range_multiplier: '1.0',
      volatility_buffer_spread_mult: '1.0',
      markets: {},
      risk_limits: {},
    },
  } as unknown as components['schemas']['SessionConfigurationResponseOutput']

  it('renders session ID and session state information', () => {
    render(
      <PinnedSessionDetailView
        sessionState={sampleSessionState}
        pinnedConfig={samplePinnedConfig}
      />,
    )

    expect(screen.getByText('Session Details & Pinned Configuration')).toBeInTheDocument()
    expect(screen.getByText('pure_market_making')).toBeInTheDocument()
    expect(screen.getByTitle('hash-config-9999')).toBeInTheDocument()
    expect(screen.getByTitle('hash-catalogue-8888')).toBeInTheDocument()
  })

  it('renders loading state when isLoading is true', () => {
    render(<PinnedSessionDetailView isLoading={true} />)
    expect(screen.getByText('Loading pinned session configuration…')).toBeInTheDocument()
  })

  it('renders error alert when error is provided', () => {
    render(<PinnedSessionDetailView error="Session does not exist" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Session does not exist')
  })

  it('renders empty message when no pinned configuration is found', () => {
    render(
      <PinnedSessionDetailView
        sessionId="sess-empty"
        pinnedConfig={null}
        isLoading={false}
      />,
    )
    expect(screen.getByText('No pinned configuration')).toBeInTheDocument()
  })

  it('triggers onClose when close button is clicked', () => {
    const onClose = vi.fn()
    render(
      <PinnedSessionDetailView
        sessionState={sampleSessionState}
        onClose={onClose}
      />,
    )

    const closeBtn = screen.getByRole('button', { name: /Close session details/i })
    fireEvent.click(closeBtn)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})

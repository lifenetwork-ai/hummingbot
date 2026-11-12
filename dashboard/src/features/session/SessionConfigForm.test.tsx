import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SessionConfigForm } from './SessionConfigForm'
import type { FormFeedbackModel } from '../../core/api/operation-feedback'

describe('SessionConfigForm inline feedback integration', () => {
  const defaultActionAvailability = {
    start: { allowed: true, detail: 'Start available' },
    switch: { allowed: true, detail: 'Switch available' },
  }

  const sampleProfilesData = {
    catalogue_hash: 'cat-1',
    market_modes: ['spot', 'perpetual', 'both'] as ('spot' | 'perpetual' | 'both')[],
    duration_modes: ['timed', 'until_stopped'],
    catalogue: {
      allow_until_stopped: true,
      default_duration_seconds: 3600,
      min_duration_seconds: 60,
      max_duration_seconds: 86400,
      random: { allow_random: true, profile_ids: ['p1', 'p2'], step_duration_seconds: 120 },
      profiles: {
        p1: { anchor: 'session_start_mid' as const, scenario: 'follow_market' as const, sideways_range: null },
      },
      markets: {},
      max_controller_drawdown_quote: '100',
      max_global_drawdown_quote: '200',
      max_total_amount_quote: '1000',
      spot_outflow: { buy_allowed: true, max_net_outflow_quote: '500', recorded_net_outflow_quote: '0' },
    },
  } as unknown as import('../../core/api/schema').components['schemas']['ProfilesResponseOutput']

  it('renders inline error on action and profileId from feedbackModel', () => {
    const feedbackModel: FormFeedbackModel = {
      feedback: {
        surface: 'inline',
        code: 'validation_error',
        requestId: 'req-val-123',
        fields: [],
        retry: 'fix_request',
        retryAt: null,
        outcomeUnknown: false,
      },
      inlineErrors: {
        action: ['action_precondition_failed'],
        profileId: ['profile_not_eligible'],
      },
      globalFields: [{ path: 'version', code: 'version_mismatch' }],
    }

    render(
      <SessionConfigForm
        initialAction="start"
        actionAvailability={defaultActionAvailability}
        profilesData={sampleProfilesData}
        onSubmitReview={vi.fn()}
        feedbackModel={feedbackModel}
      />,
    )

    // Inline error on action
    expect(screen.getByText('action_precondition_failed')).toBeInTheDocument()

    // Inline error on profileId
    expect(screen.getByText('profile_not_eligible')).toBeInTheDocument()

    // Diagnostics from FeedbackDetailsView
    expect(screen.getByText('version_mismatch')).toBeInTheDocument()
  })

  it('renders inline error on marketMode and durationMode', () => {
    const feedbackModel: FormFeedbackModel = {
      feedback: null,
      inlineErrors: {
        marketMode: ['market_mode_unsupported'],
        durationMode: ['duration_mode_invalid'],
      },
      globalFields: [],
    }

    render(
      <SessionConfigForm
        initialAction="start"
        actionAvailability={defaultActionAvailability}
        profilesData={sampleProfilesData}
        onSubmitReview={vi.fn()}
        feedbackModel={feedbackModel}
      />,
    )

    expect(screen.getByText('market_mode_unsupported')).toBeInTheDocument()
    expect(screen.getByText('duration_mode_invalid')).toBeInTheDocument()
  })
})

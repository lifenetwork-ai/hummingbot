import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { pageApiUrl, pageWrapper, setupPageApi } from '../../test/operationalTest'
import { server } from '../../test/mocks/server'
import { ProfileExplorerDialog } from './ProfileExplorerDialog'

const catalogue = {
  follow_market: { anchor: 'session_start_mid', scenario: 'follow_market', sideways_range: null,
    references: [{ pair: 'BTC-USDT', weight: '0.6' }, { pair: 'ETH-USDT', weight: '0.4' }] },
  sideways: { anchor: 'session_start_mid', scenario: 'sideways', sideways_range: null },
}

function renderExplorer(props: Partial<Parameters<typeof ProfileExplorerDialog>[0]> = {}) {
  return render(
    <ProfileExplorerDialog open onClose={() => {}} activeProfileId="follow_market" {...props} />,
    { wrapper: pageWrapper() },
  )
}

describe('ProfileExplorerDialog', () => {
  setupPageApi()

  it('renders catalogue profiles as tabs with the active one preselected', async () => {
    server.use(http.get(`${pageApiUrl}/profiles`, () => HttpResponse.json({
      catalogue_hash: 'h', process_identity: null, reporting_timezone: 'UTC',
      market_modes: ['spot'], duration_modes: ['timed'],
      catalogue: { profiles: catalogue, markets: {}, random: { profile_ids: ['follow_market'], step_duration_seconds: 120 } },
    })))
    renderExplorer()
    expect(screen.getByText('Loading catalogue…')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('tab', { name: 'follow_market •' })).toHaveAttribute('aria-selected', 'true'))
    expect(screen.getByRole('tab', { name: 'sideways' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByTestId('profile-explorer-references')).toBeInTheDocument()
    expect(screen.getByText(/Tracks a weighted market basket/i)).toBeInTheDocument()
  })

  it('recomputes the weighted move when a reference slider changes', async () => {
    server.use(http.get(`${pageApiUrl}/profiles`, () => HttpResponse.json({
      catalogue_hash: 'h', process_identity: null, reporting_timezone: 'UTC',
      market_modes: ['spot'], duration_modes: ['timed'],
      catalogue: { profiles: catalogue, markets: {}, random: { profile_ids: ['follow_market'], step_duration_seconds: 120 } },
    })))
    renderExplorer({ anchorLife: 0.012 })
    await waitFor(() => expect(screen.getByTestId('profile-explorer-move')).toBeInTheDocument())
    expect(screen.getByTestId('profile-explorer-move')).toHaveTextContent('+0.0%')
    expect(screen.getByTestId('profile-explorer-target')).toHaveTextContent('0.012')

    // jsdom does not apply arrow keys to range inputs; drive the input value directly.
    fireEvent.change(screen.getByRole('slider', { name: /BTC-USDT move/i }), { target: { value: 0.5 } })
    expect(screen.getByTestId('profile-explorer-move')).toHaveTextContent('+0.3%')
    expect(screen.getByTestId('profile-explorer-target')).toHaveTextContent('0.01204')
  })

  it('explains sideways without references and keeps the move at zero', async () => {
    server.use(http.get(`${pageApiUrl}/profiles`, () => HttpResponse.json({
      catalogue_hash: 'h', process_identity: null, reporting_timezone: 'UTC',
      market_modes: ['spot'], duration_modes: ['timed'],
      catalogue: { profiles: catalogue, markets: {}, random: { profile_ids: ['sideways'], step_duration_seconds: 120 } },
    })))
    const user = userEvent.setup()
    renderExplorer({ activeProfileId: 'sideways' })
    await waitFor(() => expect(screen.getByRole('tab', { name: 'sideways •' })).toBeInTheDocument())
    await user.click(screen.getByRole('tab', { name: 'sideways •' }))
    expect(screen.getByTestId('profile-explorer-no-references')).toBeInTheDocument()
    expect(screen.getByText(/Holds the session anchor/i)).toBeInTheDocument()
    expect(screen.getByTestId('profile-explorer-move')).toHaveTextContent('+0.0%')
  })
})

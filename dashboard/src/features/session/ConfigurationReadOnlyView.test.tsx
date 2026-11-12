import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { ConfigurationReadOnlyView } from './ConfigurationReadOnlyView'
import { completionConfiguration } from '../../test/mocks/completionFixtures'

describe('ConfigurationReadOnlyView', () => {
  it('renders pinned session configuration and current catalogue from completionConfiguration', () => {
    render(<ConfigurationReadOnlyView model={completionConfiguration} />)

    // Pinned metadata
    expect(screen.getByText('Pinned Session Configuration')).toBeInTheDocument()
    expect(screen.getByText('completion-session')).toBeInTheDocument()
    expect(screen.getByText('catalogue-pinned')).toBeInTheDocument()
    expect(screen.getByText('config-pinned')).toBeInTheDocument()

    // Active Profile Indicator
    expect(screen.getByText('RUNNING')).toBeInTheDocument()

    // Profiles
    expect(screen.getAllByText('follow_market').length).toBeGreaterThanOrEqual(1)

    // Markets & exact decimal string for risk limit
    expect(screen.getByText('9007199254740993.000001 Quote')).toBeInTheDocument()

    // No editable input fields in read-only view
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('renders explicit unavailable state when pinned configuration is missing', () => {
    const modelWithoutPinned = {
      ...completionConfiguration,
      pinned: null,
    }

    render(<ConfigurationReadOnlyView model={modelWithoutPinned} />)

    expect(
      screen.getByText(/Pinned Configuration Unavailable/),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/Catalogue is not used as a substitute/),
    ).toBeInTheDocument()
  })

  it('supports segmented tabs to switch between Pinned, Catalogue, and Diff View', async () => {
    const user = userEvent.setup()
    render(<ConfigurationReadOnlyView model={completionConfiguration} />)

    // Switch to Catalogue view
    await user.click(screen.getByRole('button', { name: /Original Catalogue/ }))
    expect(screen.getByText('Current Configuration Catalogue')).toBeInTheDocument()

    // Switch to Diff View
    await user.click(screen.getByRole('button', { name: /Detailed Diff View/ }))
    expect(screen.getByText('Parameter')).toBeInTheDocument()
    expect(screen.getByText('Pinned')).toBeInTheDocument()
    expect(screen.getAllByText('Catalogue').length).toBeGreaterThanOrEqual(1)

    // Filter to only diffs
    await user.click(screen.getByRole('button', { name: /Only show diffs/ }))
    expect(screen.getAllByText('Changed').length).toBeGreaterThanOrEqual(1)
  })

  it('displays truncated 8-character hash with copy button for long 64-character SHA256 hashes', () => {
    const longHash = 'fccfe00c5a5bd1e13426178a9c8b0e7f1234567890abcdef1234567890abcdef'
    const modelWithLongHash = {
      ...completionConfiguration,
      pinned: {
        ...completionConfiguration.pinned!,
        configHash: longHash,
        catalogueHash: longHash,
      },
      current: {
        ...completionConfiguration.current!,
        catalogueHash: longHash,
      },
    }

    render(<ConfigurationReadOnlyView model={modelWithLongHash} />)

    // Truncated to 8 chars: fccfe00c…
    expect(screen.getAllByText('fccfe00c…').length).toBeGreaterThanOrEqual(1)
    // Synchronized badge
    expect(screen.getByText('100% Synchronized with Catalogue')).toBeInTheDocument()
  })
})

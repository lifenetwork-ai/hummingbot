import { AlertCircle, Box, Cpu, Layers, Shield, X } from 'lucide-react'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'

export interface PinnedSessionDetailViewProps {
  sessionId?: string
  sessionState?: Pick<components['schemas']['SessionSummaryOutput'], 'id' | 'profile_id' | 'status'> | null
  pinnedConfig?: components['schemas']['SessionConfigurationResponseOutput'] | null
  isLoading?: boolean
  error?: string | null
  onClose?: () => void
  className?: string
}

export function PinnedSessionDetailView({
  sessionId,
  sessionState,
  pinnedConfig,
  isLoading,
  error,
  onClose,
  className,
}: PinnedSessionDetailViewProps) {
  const activeSessionId = sessionId || sessionState?.id || pinnedConfig?.session_id

  return (
    <Surface className={cn('p-5 md:p-6 space-y-4', className)}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-app-border/80 pb-3">
        <div className="flex items-center gap-2">
          <Layers size={18} className="text-app-action" />
          <h3 className="font-heavy text-base font-bold text-app-text sm:text-lg">
            Session Details & Pinned Configuration
          </h3>
        </div>
        {onClose && (
          <Button
            variant="secondary"
            onClick={onClose}
            className="h-7 w-7 p-0 rounded-full"
            aria-label="Close session details"
          >
            <X size={14} />
          </Button>
        )}
      </div>

      {activeSessionId && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-app-muted bg-app-surface-muted/30 p-2.5 rounded-card border border-app-border/60">
          <span className="font-semibold text-app-text">Session ID:</span>
          <CopyableId id={activeSessionId} copyLabel="Copy session ID" />
          {sessionState?.profile_id && (
            <span className="ml-2">
              · Profile: <strong className="text-app-text">{sessionState.profile_id}</strong>
            </span>
          )}
          {sessionState?.status && (
            <Badge tone="neutral" className="text-[10px] ml-1 uppercase">
              {sessionState.status}
            </Badge>
          )}
        </div>
      )}

      {error && (
        <div role="alert" className="rounded-card border border-app-danger/30 bg-app-danger/10 p-3 text-xs text-app-danger">
          {error}
        </div>
      )}

      {isLoading && (
        <p className="text-xs text-app-muted py-6 text-center">
          Loading pinned session configuration…
        </p>
      )}

      {!isLoading && !pinnedConfig && (
        <div className="rounded-card border border-app-border/70 bg-app-surface-muted/20 p-6 text-center">
          <AlertCircle size={22} className="mx-auto text-app-muted mb-2" />
          <h4 className="font-bold text-sm text-app-text">No pinned configuration</h4>
          <p className="mt-1 text-xs text-app-muted max-w-md mx-auto">
            This session has no pinned configuration or configuration details are no longer available.
          </p>
        </div>
      )}

      {!isLoading && pinnedConfig && (
        <div className="space-y-4 text-xs">
          {/* Metadata & hashes */}
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 text-[11px] text-app-muted border-b border-app-border/50 pb-3">
            {pinnedConfig.config_hash && (
              <div>
                <span className="block text-[10px]">Config Hash:</span>
                <CopyableId id={pinnedConfig.config_hash} truncateLength={10} copyLabel="Copy config hash" />
              </div>
            )}
            {pinnedConfig.catalogue_hash && (
              <div>
                <span className="block text-[10px]">Catalogue Hash:</span>
                <CopyableId id={pinnedConfig.catalogue_hash} truncateLength={10} copyLabel="Copy catalogue hash" />
              </div>
            )}
            <div>
              <span className="block text-[10px]">Configuration Source:</span>
              <Badge tone="brand" className="text-[10px]">{pinnedConfig.source}</Badge>
            </div>
          </div>

          <p className="text-[11px] text-app-muted">Profile context: {pinnedConfig.profile_context}</p>
          {/* Request params */}
          {pinnedConfig.request && (
            <div className="rounded bg-app-surface-muted/30 p-2.5 space-y-1">
              <span className="font-semibold text-app-text text-[11px] block">Session Launch Request:</span>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-app-muted">
                <span>Selection: <strong className="text-app-text">{pinnedConfig.request.selection}</strong></span>
                <span>Market: <strong className="text-app-text">{pinnedConfig.request.market_mode}</strong></span>
                <span>Duration: <strong className="text-app-text">{pinnedConfig.request.duration_mode}</strong>{pinnedConfig.request.duration_seconds ? ` (${pinnedConfig.request.duration_seconds}s)` : ''}</span>
                {pinnedConfig.request.step_duration_seconds && <span>Random Step: <strong className="text-app-text">{pinnedConfig.request.step_duration_seconds}s</strong></span>}
              </div>
            </div>
          )}

          {/* Pinned Profiles */}
          <div className="space-y-2">
            <h5 className="font-bold text-app-text flex items-center gap-1.5 text-xs">
              <Cpu size={13} className="text-app-action" />
              <span>Strategy Profiles (Pinned)</span>
            </h5>
            <div className="grid gap-2 sm:grid-cols-2">
              {Object.entries(pinnedConfig.catalogue?.profiles ?? {}).map(([id, profile]) => (
                <div key={id} className="rounded border border-app-border/60 p-2.5 space-y-1 bg-app-surface">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-app-text">{id}</span>
                    <Badge tone="neutral" className="text-[10px]">{profile.scenario}</Badge>
                  </div>
                  <p className="text-[10px] text-app-muted">Anchor: {profile.anchor}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Pinned Markets */}
          <div className="space-y-2">
            <h5 className="font-bold text-app-text flex items-center gap-1.5 text-xs">
              <Box size={13} className="text-app-action" />
              <span>Pinned Markets & Budgets</span>
            </h5>
            <div className="grid gap-2 sm:grid-cols-2">
              {Object.entries(pinnedConfig.catalogue?.markets ?? {}).map(([market, mConfig]) => (
                <div key={market} className="rounded border border-app-border/60 p-2.5 space-y-1 bg-app-surface">
                  <div className="flex items-center justify-between">
                    <span className="font-bold uppercase text-app-text">{market}</span>
                    <span className="font-mono text-[10px] text-app-muted">{mConfig.connector_name}</span>
                  </div>
                  <p className="text-[10px] text-app-muted">
                    Budget: <strong className="font-mono text-app-text">{mConfig.total_amount_quote ?? '—'} Quote</strong> · Leverage: {mConfig.leverage}x
                  </p>
                  <p className="text-[10px] text-app-muted">
                    Buy Spreads: <span className="font-mono">{mConfig.buy_spreads?.join(', ') || '—'}</span> · Sell: <span className="font-mono">{mConfig.sell_spreads?.join(', ') || '—'}</span>
                  </p>
                </div>
              ))}
            </div>
          </div>

          {/* Pinned Risk Limits */}
          <div className="rounded border border-app-border/60 p-2.5 bg-app-surface space-y-1">
            <h5 className="font-bold text-app-text flex items-center gap-1.5 text-xs">
              <Shield size={13} className="text-app-action" />
              <span>Pinned Risk Limits</span>
            </h5>
            <div className="grid grid-cols-2 gap-2 text-[10px] text-app-muted">
              <div>Max total quote: <strong className="font-mono text-app-text">{pinnedConfig.catalogue.max_total_amount_quote ?? '—'}</strong></div>
              <div>Max controller drawdown: <strong className="font-mono text-app-text">{pinnedConfig.catalogue.max_controller_drawdown_quote ?? '—'}</strong></div>
              <div>Max global drawdown: <strong className="font-mono text-app-text">{pinnedConfig.catalogue.max_global_drawdown_quote ?? '—'}</strong></div>
              <div>Spot outflow: <strong className="text-app-text">{pinnedConfig.catalogue.spot_outflow ? `${pinnedConfig.catalogue.spot_outflow.max_net_outflow_quote} Quote` : 'Not applied'}</strong></div>
            </div>
          </div>
        </div>
      )}
    </Surface>
  )
}

import { useState } from 'react'
import {
  AlertCircle,
  AlertTriangle,
  Box,
  CheckCircle2,
  Cpu,
  FileDiff,
  FileText,
  Filter,
  Layers,
  Shield,
  Sliders,
} from 'lucide-react'
import type { ConfigurationReviewModel, ConfigurationSnapshot } from '../session-control/configuration-review'
import { Badge } from '../../shared/components/Badge'
import { CopyableId } from '../../shared/components/CopyableId'
import { Surface } from '../../shared/components/Surface'
import { cn } from '../../shared/utils/cn'
import { formatSpread, formatSpreadList, formatTechnicalNumber } from '../../shared/utils/formatters'

export interface ConfigurationReadOnlyViewProps {
  model?: ConfigurationReviewModel | null
  currentSnapshot?: ConfigurationSnapshot | null
  pinnedSnapshot?: ConfigurationSnapshot | null
  className?: string
}

/** A single field with inline diff highlight if pinned differs from catalogue */
function InlineDiffField({
  label,
  value,
  diffValue,
  unit,
  className,
}: {
  label: string
  value: React.ReactNode
  diffValue?: React.ReactNode
  unit?: string
  className?: string
}) {
  const hasDiff = diffValue !== undefined && diffValue !== null && String(value) !== String(diffValue)

  return (
    <div className={cn('text-[11px]', className)}>
      <span className="block text-[10px] text-app-muted">{label}:</span>
      {hasDiff ? (
        <div className="mt-0.5 rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5">
          <span className="font-mono font-bold text-app-text">
            {value}
            {unit ? ` ${unit}` : ''}
          </span>
          <span className="block text-[10px] text-amber-500 font-medium">
            (New catalogue: {diffValue}{unit ? ` ${unit}` : ''})
          </span>
        </div>
      ) : (
        <span className="font-mono font-bold text-app-text">
          {value}
          {unit ? ` ${unit}` : ''}
        </span>
      )}
    </div>
  )
}

function SnapshotDisplay({
  snapshot,
  title,
  subtitle,
  emptyMessage,
  comparatorSnapshot,
}: {
  snapshot?: ConfigurationSnapshot | null
  title: string
  subtitle: string
  emptyMessage: string
  comparatorSnapshot?: ConfigurationSnapshot | null
}) {
  if (!snapshot) {
    return (
      <div className="rounded-card border border-app-border/80 bg-app-surface-muted/20 p-5 text-center">
        <div className="mx-auto mb-2 flex h-9 w-9 items-center justify-center rounded-full bg-app-surface-muted text-app-muted">
          <AlertCircle size={18} />
        </div>
        <h4 className="font-heavy text-sm font-bold text-app-text">{title}</h4>
        <p className="mt-1 text-xs text-app-muted max-w-md mx-auto leading-relaxed">
          {emptyMessage}
        </p>
      </div>
    )
  }

  const { catalogueHash, configHash, sessionId, activeProfileId, request, profiles, markets, random, riskLimits } = snapshot

  // Deterministic stable sorting of profiles
  const sortedProfiles = [...profiles].sort((a, b) => a.id.localeCompare(b.id))

  // Find comparator market/profile if available for inline diff highlighting
  const getCatMarket = (m: string) => comparatorSnapshot?.markets.find((item) => item.market === m)?.value

  return (
    <div className="space-y-4 rounded-card border border-app-border/80 bg-app-surface-muted/20 p-4 sm:p-5">
      {/* Header & Hashes */}
      <div className="border-b border-app-border/60 pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="font-heavy text-base font-bold text-app-text flex items-center gap-2">
              <FileText size={16} className="text-app-action" />
              <span>{title}</span>
            </h4>
            <p className="mt-0.5 text-xs text-app-muted">{subtitle}</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={snapshot.source === 'pinned_session' ? 'brand' : 'neutral'} className="text-xs">
              Source: {snapshot.source}
            </Badge>
          </div>
        </div>

        <p className="mt-2 text-[11px] text-app-muted">Profile context: {snapshot.profileContext ?? 'Unknown'}</p>
        <div className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4 text-app-muted">
          {sessionId && (
            <div>
              <span className="block text-[10px] text-app-muted">Session ID:</span>
              <CopyableId
                id={sessionId}
                truncateLength={sessionId.length > 24 ? 8 : undefined}
                copyLabel="Copy session ID"
              />
            </div>
          )}
          {activeProfileId && (
            <div>
              <span className="block text-[10px] text-app-muted">Active profile:</span>
              <span className="font-mono font-bold text-app-text">{activeProfileId}</span>
            </div>
          )}
          {configHash && (
            <div>
              <span className="block text-[10px] text-app-muted">Config Hash (configHash):</span>
              <CopyableId
                id={configHash}
                truncateLength={configHash.length > 24 ? 8 : undefined}
                copyLabel="Copy config hash"
              />
            </div>
          )}
          {catalogueHash && (
            <div>
              <span className="block text-[10px] text-app-muted">Catalogue Hash (catalogueHash):</span>
              <CopyableId
                id={catalogueHash}
                truncateLength={catalogueHash.length > 24 ? 8 : undefined}
                copyLabel="Copy catalogue hash"
              />
            </div>
          )}
        </div>

        {request && (
          <div className="mt-2.5 rounded bg-app-surface/60 border border-app-border/50 p-2 text-xs">
            <span className="font-semibold text-app-text text-[11px] block mb-1">Request Parameters:</span>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-app-muted">
              <span>Selection: <strong className="text-app-text">{request.selection}</strong></span>
              <span>Market: <strong className="text-app-text">{request.market_mode}</strong></span>
              <span>Duration: <strong className="text-app-text">{request.duration_mode}</strong>{request.duration_seconds ? ` (${request.duration_seconds}s)` : ''}</span>
              {request.step_duration_seconds && <span>Random Step: <strong className="text-app-text">{request.step_duration_seconds}s</strong></span>}
            </div>
          </div>
        )}
      </div>

      {/* Profiles list */}
      <div className="space-y-2">
        <h5 className="text-xs font-bold uppercase tracking-wider text-app-text flex items-center gap-1.5">
          <Cpu size={14} className="text-app-action" />
          <span>Strategy Profiles</span>
        </h5>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {sortedProfiles.map(({ id, value }) => {
            const isActive = activeProfileId === id
            const isRedundantScenario = value.scenario && value.scenario.toLowerCase() === id.toLowerCase()

            return (
              <div
                key={id}
                className={cn(
                  'rounded-card border p-3 text-xs space-y-1.5 transition-all',
                  isActive
                    ? 'border-app-action bg-app-action/5 ring-1 ring-app-action shadow-xs'
                    : 'border-app-border/70 bg-app-surface'
                )}
              >
                <div className="flex items-center justify-between border-b border-app-border/40 pb-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="font-bold text-app-text">{id}</span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {isActive && (
                      <span className="inline-flex items-center gap-1 rounded-pill bg-app-action px-2 py-0.5 text-[10px] font-bold text-white shadow-xs">
                        <span className="h-1.5 w-1.5 rounded-full bg-white animate-pulse" />
                        RUNNING
                      </span>
                    )}
                    {!isRedundantScenario && value.scenario && (
                      <Badge tone="neutral" className="text-[10px] font-mono">
                        {value.scenario}
                      </Badge>
                    )}
                  </div>
                </div>
                <div className="text-[11px] text-app-muted space-y-0.5">
                  <p>Anchor: <span className="font-mono text-app-text">{value.anchor}</span></p>
                  {value.references && value.references.length > 0 && (
                    <div>
                      <span className="block font-medium text-app-text mt-1">Reference Pairs:</span>
                      <ul className="list-disc list-inside pl-1 space-y-0.5">
                        {value.references.map((ref, idx) => (
                          <li key={idx} className="font-mono text-[10px]">
                            {ref.pair} · weight: {ref.weight}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {value.sideways_range && (
                    <p>Sideways Range: <span className="font-mono text-app-text">{value.sideways_range.lower_price} - {value.sideways_range.upper_price}</span></p>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Markets list */}
      <div className="space-y-2">
        <h5 className="text-xs font-bold uppercase tracking-wider text-app-text flex items-center gap-1.5">
          <Box size={14} className="text-app-action" />
          <span>Markets Configuration</span>
        </h5>
        <div className="grid gap-3 lg:grid-cols-2">
          {markets.map(({ market, value }) => {
            const catVal = getCatMarket(market)

            return (
              <div key={market} className="rounded-card border border-app-border/70 bg-app-surface p-3.5 text-xs space-y-2.5">
                <div className="flex items-center justify-between border-b border-app-border/50 pb-2">
                  <div>
                    <span className="font-bold text-sm text-app-text uppercase">{market}</span>
                    <span className="ml-2 text-[11px] text-app-muted font-mono">{value.connector_name}</span>
                  </div>
                  <Badge tone="neutral" className="text-[10px] font-mono">
                    {value.controller_name}
                  </Badge>
                </div>

                <div className="grid grid-cols-2 gap-2 text-[11px] text-app-muted">
                  <InlineDiffField
                    label="Allocated Budget"
                    value={value.total_amount_quote ?? '—'}
                    diffValue={catVal?.total_amount_quote}
                    unit="Quote"
                  />

                  <InlineDiffField
                    label="Leverage & Margin"
                    value={`${value.leverage}x (${value.margin_mode})`}
                    diffValue={catVal ? `${catVal.leverage}x (${catVal.margin_mode})` : undefined}
                  />

                  <InlineDiffField
                    label="Buy Spreads"
                    value={formatSpreadList(value.buy_spreads)}
                    diffValue={catVal?.buy_spreads ? formatSpreadList(catVal.buy_spreads) : undefined}
                  />

                  <InlineDiffField
                    label="Sell Spreads"
                    value={formatSpreadList(value.sell_spreads)}
                    diffValue={catVal?.sell_spreads ? formatSpreadList(catVal.sell_spreads) : undefined}
                  />

                  <InlineDiffField
                    label="Buy Amounts Pct"
                    value={value.buy_amounts_pct?.join(', ') ?? 'Default'}
                    diffValue={catVal?.buy_amounts_pct?.join(', ')}
                  />

                  <InlineDiffField
                    label="Sell Amounts Pct"
                    value={value.sell_amounts_pct?.join(', ') ?? 'Default'}
                    diffValue={catVal?.sell_amounts_pct?.join(', ')}
                  />

                  <InlineDiffField
                    label="Cooldown"
                    value={`${value.cooldown_time}s`}
                    diffValue={catVal ? `${catVal.cooldown_time}s` : undefined}
                  />

                  <InlineDiffField
                    label="Executor Refresh"
                    value={`${value.executor_refresh_time}s`}
                    diffValue={catVal ? `${catVal.executor_refresh_time}s` : undefined}
                  />
                </div>

                {value.inventory_skew && (
                  <div className="rounded bg-app-surface-muted/40 p-2 text-[10px] text-app-muted space-y-0.5">
                    <span className="font-semibold text-app-text block">Inventory Skew Policy:</span>
                    <p>
                      Target base pct: {formatTechnicalNumber(value.inventory_skew.target_base_pct)} · Range multiplier: {formatTechnicalNumber(value.inventory_skew.inventory_range_multiplier)}
                    </p>
                  </div>
                )}

                {value.quote_guards && (
                  <div className="rounded bg-app-surface-muted/40 p-2 text-[10px] text-app-muted space-y-0.5">
                    <span className="font-semibold text-app-text block">Quote Guard Policy:</span>
                    <p>
                      Max deviation: {formatSpread(value.quote_guards.max_deviation_bps)} · Enabled: {value.quote_guards.enabled ? 'Yes' : 'No'}
                    </p>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Two-Column Responsive Layout: Random Pool + Risk Limits */}
      <div className="grid gap-4 lg:grid-cols-2">
        {/* Random Pool & Step Duration */}
        {random ? (
          <div className="rounded-card border border-app-border/70 bg-app-surface p-3.5 text-xs space-y-2">
            <h5 className="font-bold text-app-text flex items-center gap-1.5">
              <Sliders size={13} className="text-app-action" />
              <span>Random Pool Configuration</span>
            </h5>
            <div className="grid gap-2 sm:grid-cols-3 text-[11px] text-app-muted">
              <div>
                <span className="block text-[10px]">Allow Random:</span>
                <span className="font-semibold text-app-text">{random.profile_ids?.length > 0 ? 'Yes' : 'No'}</span>
              </div>
              <div>
                <span className="block text-[10px]">Step duration:</span>
                <span className="font-mono font-bold text-app-text">{random.step_duration_seconds}s</span>
              </div>
              <div>
                <span className="block text-[10px]">Profile pool:</span>
                <span className="font-mono text-app-text">{random.profile_ids?.join(', ') || '—'}</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="rounded-card border border-app-border/70 bg-app-surface p-3.5 text-xs text-app-muted flex items-center justify-center">
            <span>Random Pool not applied</span>
          </div>
        )}

        {/* Risk Limits */}
        <div className="rounded-card border border-app-border/70 bg-app-surface p-3.5 text-xs space-y-2">
          <h5 className="font-bold text-app-text flex items-center gap-1.5">
            <Shield size={13} className="text-app-action" />
            <span>Risk Limits</span>
          </h5>
          <div className="grid gap-2 sm:grid-cols-2 text-[11px] text-app-muted">
            <InlineDiffField
              label="Max Total Budget"
              value={riskLimits.max_total_amount_quote ?? '—'}
              diffValue={comparatorSnapshot?.riskLimits.max_total_amount_quote}
              unit="Quote"
            />
            <InlineDiffField
              label="Max Controller Drawdown"
              value={riskLimits.max_controller_drawdown_quote ?? '—'}
              diffValue={comparatorSnapshot?.riskLimits.max_controller_drawdown_quote}
              unit="Quote"
            />
            <InlineDiffField
              label="Max Global Drawdown"
              value={riskLimits.max_global_drawdown_quote ?? '—'}
              diffValue={comparatorSnapshot?.riskLimits.max_global_drawdown_quote}
              unit="Quote"
            />
            <div>
              <span className="block text-[10px]">Spot Outflow:</span>
              <span className="font-mono font-bold text-app-text">
                {riskLimits.spot_outflow ? `${riskLimits.spot_outflow.max_net_outflow_quote} Quote` : 'Not applied'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Side-by-side Audit Diff Table */
function SideBySideDiffTable({
  pinned,
  current,
}: {
  pinned?: ConfigurationSnapshot | null
  current?: ConfigurationSnapshot | null
}) {
  const [onlyDiffs, setOnlyDiffs] = useState(false)

  if (!pinned || !current) {
    return (
      <div className="rounded-card border border-app-border/80 bg-app-surface-muted/20 p-5 text-center text-xs text-app-muted">
        Both Pinned and Catalogue snapshots are required to display the diff table.
      </div>
    )
  }

  type DiffRow = {
    group: string
    name: string
    pinnedVal: string
    catVal: string
    isDiff: boolean
  }

  const rows: DiffRow[] = []

  // Metadata
  rows.push({
    group: 'Identity',
    name: 'Catalogue hash (catalogueHash)',
    pinnedVal: pinned.catalogueHash ?? '—',
    catVal: current.catalogueHash ?? '—',
    isDiff: pinned.catalogueHash !== current.catalogueHash,
  })

  // Markets
  for (const pMarket of pinned.markets) {
    const cMarket = current.markets.find((m) => m.market === pMarket.market)
    const pVal = pMarket.value
    const cVal = cMarket?.value

    rows.push({
      group: `Market: ${pMarket.market}`,
      name: 'Allocated Budget (Quote)',
      pinnedVal: `${pVal.total_amount_quote ?? '—'} Quote`,
      catVal: cVal ? `${cVal.total_amount_quote ?? '—'} Quote` : '—',
      isDiff: pVal.total_amount_quote !== cVal?.total_amount_quote,
    })

    rows.push({
      group: `Market: ${pMarket.market}`,
      name: 'Leverage & Margin',
      pinnedVal: `${pVal.leverage}x (${pVal.margin_mode})`,
      catVal: cVal ? `${cVal.leverage}x (${cVal.margin_mode})` : '—',
      isDiff: pVal.leverage !== cVal?.leverage || pVal.margin_mode !== cVal?.margin_mode,
    })

    rows.push({
      group: `Market: ${pMarket.market}`,
      name: 'Buy Spreads',
      pinnedVal: formatSpreadList(pVal.buy_spreads),
      catVal: cVal ? formatSpreadList(cVal.buy_spreads) : '—',
      isDiff: JSON.stringify(pVal.buy_spreads) !== JSON.stringify(cVal?.buy_spreads),
    })

    rows.push({
      group: `Market: ${pMarket.market}`,
      name: 'Sell Spreads',
      pinnedVal: formatSpreadList(pVal.sell_spreads),
      catVal: cVal ? formatSpreadList(cVal.sell_spreads) : '—',
      isDiff: JSON.stringify(pVal.sell_spreads) !== JSON.stringify(cVal?.sell_spreads),
    })

    rows.push({
      group: `Market: ${pMarket.market}`,
      name: 'Cooldown',
      pinnedVal: `${pVal.cooldown_time}s`,
      catVal: cVal ? `${cVal.cooldown_time}s` : '—',
      isDiff: pVal.cooldown_time !== cVal?.cooldown_time,
    })
  }

  // Risk limits
  rows.push({
    group: 'Risk Limits',
    name: 'Max Total Budget',
    pinnedVal: `${pinned.riskLimits.max_total_amount_quote ?? '—'} Quote`,
    catVal: `${current.riskLimits.max_total_amount_quote ?? '—'} Quote`,
    isDiff: pinned.riskLimits.max_total_amount_quote !== current.riskLimits.max_total_amount_quote,
  })

  rows.push({
    group: 'Risk Limits',
    name: 'Max Controller Drawdown',
    pinnedVal: `${pinned.riskLimits.max_controller_drawdown_quote ?? '—'} Quote`,
    catVal: `${current.riskLimits.max_controller_drawdown_quote ?? '—'} Quote`,
    isDiff: pinned.riskLimits.max_controller_drawdown_quote !== current.riskLimits.max_controller_drawdown_quote,
  })

  rows.push({
    group: 'Risk Limits',
    name: 'Max Global Drawdown',
    pinnedVal: `${pinned.riskLimits.max_global_drawdown_quote ?? '—'} Quote`,
    catVal: `${current.riskLimits.max_global_drawdown_quote ?? '—'} Quote`,
    isDiff: pinned.riskLimits.max_global_drawdown_quote !== current.riskLimits.max_global_drawdown_quote,
  })

  const displayedRows = onlyDiffs ? rows.filter((r) => r.isDiff) : rows

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="text-app-muted">
          Total: <strong>{rows.length}</strong> parameters ({rows.filter((r) => r.isDiff).length} changed)
        </span>
        <button
          type="button"
          onClick={() => setOnlyDiffs(!onlyDiffs)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors',
            onlyDiffs
              ? 'border-amber-500/50 bg-amber-500/10 text-amber-500'
              : 'border-app-border bg-app-surface text-app-muted hover:text-app-text'
          )}
        >
          <Filter size={12} />
          <span>Only show diffs ({rows.filter((r) => r.isDiff).length})</span>
        </button>
      </div>

      <div className="overflow-hidden rounded-card border border-app-border">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-app-border bg-app-surface-muted/60 text-app-muted font-semibold">
              <th className="p-3 w-1/3">Parameter</th>
              <th className="p-3 w-1/4">Pinned</th>
              <th className="p-3 w-1/4">Catalogue</th>
              <th className="p-3 w-28 text-center">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-app-border/60">
            {displayedRows.length === 0 ? (
              <tr>
                <td colSpan={4} className="p-6 text-center text-app-muted">
                  No differences found. Both configurations are 100% synchronized!
                </td>
              </tr>
            ) : (
              displayedRows.map((row, idx) => (
                <tr key={idx} className={row.isDiff ? 'bg-amber-500/5' : ''}>
                  <td className="p-3 font-medium text-app-text">
                    <span className="block text-[10px] text-app-muted">{row.group}</span>
                    <span>{row.name}</span>
                  </td>
                  <td className="p-3 font-mono text-app-text">{row.pinnedVal}</td>
                  <td className="p-3 font-mono text-app-text">{row.catVal}</td>
                  <td className="p-3 text-center">
                    {row.isDiff ? (
                      <Badge tone="warning" className="text-[10px]">
                        Changed
                      </Badge>
                    ) : (
                      <Badge tone="neutral" className="text-[10px]">
                        Match
                      </Badge>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function ConfigurationReadOnlyView({
  model,
  currentSnapshot,
  pinnedSnapshot,
  className,
}: ConfigurationReadOnlyViewProps) {
  const current = model ? model.current : currentSnapshot
  const pinned = model ? model.pinned : pinnedSnapshot

  // Segmented view mode: 'pinned' (default) | 'catalogue' | 'diff'
  const [viewMode, setViewMode] = useState<'pinned' | 'catalogue' | 'diff'>('pinned')

  // Hash comparison
  const isSync = Boolean(
    pinned?.catalogueHash && current?.catalogueHash && pinned.catalogueHash === current.catalogueHash
  )
  const isHashAvailable = Boolean(pinned?.catalogueHash && current?.catalogueHash)

  return (
    <Surface className={cn('p-5 md:p-6 space-y-5', className)}>
      {/* Top Header & Segmented Controls */}
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-app-border/80 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <Layers size={18} className="text-app-action" />
            <h3 className="font-heavy text-lg font-bold text-app-text">
              Configuration Review (Read-Only)
            </h3>
          </div>
          <p className="mt-1 text-xs text-app-muted max-w-xl">
            All parameters preserved directly from backend DTOs.
          </p>
        </div>

        {/* Sync Status Badge */}
        <div className="flex items-center gap-2 shrink-0">
          {pinned && isHashAvailable && (
            <Badge
              tone={isSync ? 'success' : 'warning'}
              className="px-2.5 py-1 text-xs font-semibold inline-flex items-center gap-1.5"
            >
              {isSync ? (
                <>
                  <CheckCircle2 size={13} className="text-emerald-500" />
                  <span>100% Synchronized with Catalogue</span>
                </>
              ) : (
                <>
                  <AlertTriangle size={13} className="text-amber-500" />
                  <span>Version Mismatch vs Catalogue</span>
                </>
              )}
            </Badge>
          )}
        </div>
      </div>

      {/* Segmented Switcher */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-app-border/60 pb-3">
        <div className="flex items-center rounded-lg border border-app-border bg-app-surface-muted/50 p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setViewMode('pinned')}
            className={cn(
              'rounded-md px-3 py-1.5 font-medium transition-all',
              viewMode === 'pinned'
                ? 'bg-app-surface text-app-action shadow-xs font-bold'
                : 'text-app-muted hover:text-app-text'
            )}
          >
            Pinned & Inline Diff
          </button>
          <button
            type="button"
            onClick={() => setViewMode('catalogue')}
            className={cn(
              'rounded-md px-3 py-1.5 font-medium transition-all',
              viewMode === 'catalogue'
                ? 'bg-app-surface text-app-action shadow-xs font-bold'
                : 'text-app-muted hover:text-app-text'
            )}
          >
            Original Catalogue
          </button>
          <button
            type="button"
            onClick={() => setViewMode('diff')}
            className={cn(
              'rounded-md px-3 py-1.5 font-medium transition-all inline-flex items-center gap-1',
              viewMode === 'diff'
                ? 'bg-app-surface text-app-action shadow-xs font-bold'
                : 'text-app-muted hover:text-app-text'
            )}
          >
            <FileDiff size={13} />
            <span>Detailed Diff View</span>
          </button>
        </div>

        <div className="text-[11px] text-app-muted">
          {viewMode === 'pinned' && (
            <span>Default: displays executing configuration with highlighted diffs.</span>
          )}
          {viewMode === 'catalogue' && <span>Displays template catalogue broadcast by backend.</span>}
          {viewMode === 'diff' && <span>Visual side-by-side audit table between Pinned and Catalogue.</span>}
        </div>
      </div>

      {/* Main View Area */}
      <div>
        {viewMode === 'pinned' && (
          <section aria-label="Pinned session configuration">
            <SnapshotDisplay
              snapshot={pinned}
              comparatorSnapshot={current}
              title="Pinned Session Configuration"
              subtitle="Configuration bound to the active session or preserved from session creation."
              emptyMessage="Pinned Configuration Unavailable. No session is currently running or session has no pinned configuration. (Note: Catalogue is not used as a substitute)."
            />
          </section>
        )}

        {viewMode === 'catalogue' && (
          <section aria-label="Current configuration catalogue">
            <SnapshotDisplay
              snapshot={current}
              title="Current Configuration Catalogue"
              subtitle="Profiles and market parameters broadcast by the system catalogue."
              emptyMessage="Current configuration catalogue is not loaded or unavailable."
            />
          </section>
        )}

        {viewMode === 'diff' && (
          <section aria-label="Detailed diff table">
            <SideBySideDiffTable pinned={pinned} current={current} />
          </section>
        )}
      </div>
    </Surface>
  )
}

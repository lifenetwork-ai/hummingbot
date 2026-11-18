import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp, Clock, Filter, List, PenLine, RotateCcw } from 'lucide-react'
import type { AuditFilters, CommandFilters, EventFilters } from '../history/filters'
import { DEFAULT_HISTORY_PAGE_SIZE, EVENT_TYPES } from '../history/filters'
import type { components } from '../../core/api/schema'
import { Badge } from '../../shared/components/Badge'
import { Button } from '../../shared/components/Button'
import { DateTimeInput } from '../../shared/components/DateTimeInput'
import { Input } from '../../shared/components/Input'
import { Select } from '../../shared/components/Select'
import { cn } from '../../shared/utils/cn'

export type HistoryFiltersBarProps =
  | {
      mode: 'commands'
      filters: CommandFilters
      onChange: (filters: CommandFilters) => void
      validationErrors?: Record<string, string>
      className?: string
    }
  | {
      mode: 'events'
      filters: EventFilters
      onChange: (filters: EventFilters) => void
      validationErrors?: Record<string, string>
      className?: string
    }
  | {
      mode: 'audit'
      filters: AuditFilters
      onChange: (filters: AuditFilters) => void
      validationErrors?: Record<string, string>
      accounts?: Pick<components['schemas']['AccountResponseOutput'], 'id' | 'email' | 'role'>[]
      exchanges?: string[]
      connectors?: string[]
      className?: string
    }

const AUDIT_ACTION_OPTIONS = [
  { value: '', label: 'All actions' },
  // Access & Accounts
  { value: 'login', label: 'User login' },
  { value: 'access_denied', label: 'Access denied' },
  { value: 'account_added', label: 'Account added' },
  { value: 'account_updated', label: 'Account updated' },
  { value: 'account_bound', label: 'Account bound' },
  // Credentials & API Keys
  { value: 'credentials_set', label: 'Exchange credentials set' },
  { value: 'credentials_disconnect', label: 'Exchange disconnect' },
  { value: 'credentials_disconnect_progress', label: 'Disconnect progress' },
  { value: 'credentials_close_submitted', label: 'Close position order' },
  // Trading Session
  { value: 'trading_start', label: 'Start trading session' },
  { value: 'trading_stop', label: 'Stop trading session' },
  { value: 'trading_switch', label: 'Switch trading session' },
  // Recovery
  { value: 'recovery_check_requested', label: 'Recovery check requested' },
  { value: 'recovery_check_completed', label: 'Recovery check completed' },
  { value: 'recovery_check_failed', label: 'Recovery check failed' },
  { value: 'recovery_reset_requested', label: 'Recovery reset requested' },
  { value: 'recovery_reset_completed', label: 'Recovery reset completed' },
  { value: 'recovery_reset_failed', label: 'Recovery reset failed' },
  { value: 'recovery_position_repaired', label: 'Recovery position repaired' },
  { value: 'automatic_recovery_paused', label: 'Automatic recovery paused' },
  { value: 'automatic_recovery_order_verified', label: 'Recovery order verified' },
  { value: 'automatic_recovery_order_rejected', label: 'Recovery order rejected' },
  { value: 'automatic_recovery_resumed', label: 'Automatic recovery resumed' },
  // Reconciliation
  { value: 'reconciliation_requested', label: 'Reconciliation requested' },
]

const AUDIT_OUTCOME_OPTIONS = [
  { value: '', label: 'All outcomes' },
  { value: 'succeeded', label: 'Succeeded' },
  { value: 'success', label: 'Success' },
  { value: 'failed', label: 'Failed' },
  { value: 'failure', label: 'Failure' },
  { value: 'denied', label: 'Denied' },
  { value: 'accepted', label: 'Accepted' },
  { value: 'applying', label: 'Applying' },
]

const AUDIT_ENVIRONMENT_OPTIONS = [
  { value: '', label: 'All environments' },
  { value: 'live', label: 'Live' },
  { value: 'demo', label: 'Demo / Paper' },
]

const DEFAULT_EXCHANGES = ['okx', 'bybit', 'binance']

const DEFAULT_CONNECTORS = [
  { value: 'spot', label: 'Spot' },
  { value: 'perpetual', label: 'Perpetual Futures' },
  { value: 'okx', label: 'okx' },
  { value: 'okx_perpetual', label: 'okx_perpetual' },
  { value: 'bybit', label: 'bybit' },
  { value: 'bybit_perpetual', label: 'bybit_perpetual' },
  { value: 'binance', label: 'binance' },
  { value: 'binance_perpetual', label: 'binance_perpetual' },
]

const HISTORY_LIMIT_OPTIONS = [
  { value: '10', label: '10 items (default)' },
  { value: '25', label: '25 items' },
  { value: '50', label: '50 items' },
  { value: '100', label: '100 items' },
  { value: '200', label: '200 items' },
  { value: '500', label: '500 items (max)' },
]

/** Audit filters hidden behind the "More filters" disclosure; rarely changed day to day. */
const AUDIT_ADVANCED_KEYS = ['exchange', 'connector', 'environment', 'operation_id', 'target_id', 'limit'] as const

const QUICK_PRESETS = [
  { id: '1h', windowMs: 60 * 60 * 1000 },
  { id: '24h', windowMs: 24 * 60 * 60 * 1000 },
  { id: '7d', windowMs: 7 * 24 * 60 * 60 * 1000 },
  { id: '30d', windowMs: 30 * 24 * 60 * 60 * 1000 },
] as const

type QuickPresetId = 'all' | (typeof QUICK_PRESETS)[number]['id'] | 'today'
type TimeRangeState = QuickPresetId | 'custom'

/** Preset timestamps are absolute instants; only highlight while they still match "last N" within this tolerance. */
const PRESET_TOLERANCE_MS = 5 * 60 * 1000

/**
 * Derive the highlighted quick-range segment from the current from/to values.
 * Returns 'custom' when a range is set but matches no preset, so the segmented
 * control always reflects the actual query instead of the last click.
 */
function detectTimeRange(filters: Pick<AuditFilters, 'from_time' | 'to_time'>): TimeRangeState {
  const { from_time, to_time } = filters
  if (!from_time && !to_time) return 'all'
  if (!from_time || !to_time) return 'custom'
  const fromMs = Date.parse(from_time)
  const toMs = Date.parse(to_time)
  if (!Number.isFinite(fromMs) || !Number.isFinite(toMs)) return 'custom'
  const now = Date.now()
  for (const preset of QUICK_PRESETS) {
    if (Math.abs(toMs - now) <= PRESET_TOLERANCE_MS && Math.abs(now - fromMs - preset.windowMs) <= PRESET_TOLERANCE_MS) {
      return preset.id
    }
  }
  const today = new Date()
  const startOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 0, 0, 0, 0).getTime()
  const endOfDay = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 23, 59, 59, 999).getTime()
  if (fromMs === startOfDay && toMs === endOfDay) return 'today'
  return 'custom'
}

/** Count applied filters for the header badge; `limit` is a page-size knob, not a narrowing filter. */
function countActiveFilters(filters: Record<string, unknown>): number {
  return Object.keys(filters).filter((key) => key !== 'limit' && filters[key] !== '' && filters[key] !== undefined).length
}

function FiltersHeader({ title, activeCount, onReset }: { title: string; activeCount: number; onReset: () => void }) {
  return (
    <div className="flex items-center justify-between border-b border-app-border/50 pb-2">
      <span className="font-semibold text-app-text flex items-center gap-1.5">
        <Filter size={13} className="text-app-action" />
        <span>{title}</span>
        {activeCount > 0 && (
          <Badge tone="brand" className="ml-1 px-2 py-0.5 text-[10px]">
            {activeCount} active
          </Badge>
        )}
      </span>
      <Button variant="secondary" onClick={onReset} className="h-7 px-2.5 text-[11px] gap-1.5">
        <RotateCcw size={11} />
        <span>{activeCount > 0 ? `Reset (${activeCount})` : 'Reset filters'}</span>
      </Button>
    </div>
  )
}

function CommandsFiltersBar({
  filters,
  onChange,
  validationErrors = {},
  className,
}: Extract<HistoryFiltersBarProps, { mode: 'commands' }>) {
  const update = (patch: Partial<CommandFilters>) => {
    const next = { ...filters, ...patch }
    // Remove empty strings
    Object.keys(next).forEach((key) => {
      const k = key as keyof CommandFilters
      if (next[k] === '' || next[k] === undefined) delete next[k]
    })
    onChange(next)
  }

  return (
    <div className={cn('rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3 text-xs space-y-2.5', className)}>
      <FiltersHeader
        title="Commands Filter"
        activeCount={countActiveFilters(filters)}
        onReset={() => onChange({ limit: DEFAULT_HISTORY_PAGE_SIZE })}
      />

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          id="cmd-action-filter"
          label="Action"
          sizeVariant="sm"
          value={filters.action ?? ''}
          onChange={(e) => update({ action: (e.target.value as CommandFilters['action']) || undefined })}
          options={[
            { value: '', label: 'All actions' },
            { value: 'start', label: 'Start' },
            { value: 'switch', label: 'Switch' },
            { value: 'stop', label: 'Stop' },
          ]}
        />

        <Select
          id="cmd-status-filter"
          label="Status"
          sizeVariant="sm"
          value={filters.status ?? ''}
          onChange={(e) => update({ status: (e.target.value as CommandFilters['status']) || undefined })}
          options={[
            { value: '', label: 'All statuses' },
            { value: 'applying', label: 'Applying' },
            { value: 'accepted', label: 'Accepted' },
            { value: 'succeeded', label: 'Succeeded' },
            { value: 'failed', label: 'Failed' },
            { value: 'superseded', label: 'Superseded' },
          ]}
        />

        <Input
          label="Session ID"
          sizeVariant="sm"
          placeholder="Filter by session_id…"
          value={filters.session_id ?? ''}
          onChange={(e) => update({ session_id: e.target.value || undefined })}
        />

        <Select
          id="cmd-limit-filter"
          label="Limit"
          sizeVariant="sm"
          value={String(filters.limit ?? DEFAULT_HISTORY_PAGE_SIZE)}
          onChange={(e) => update({ limit: e.target.value ? Number(e.target.value) : undefined })}
          options={HISTORY_LIMIT_OPTIONS}
          error={validationErrors.limit}
        />
      </div>
    </div>
  )
}

function EventsFiltersBar({
  filters,
  onChange,
  className,
}: Extract<HistoryFiltersBarProps, { mode: 'events' }>) {
  const update = (patch: Partial<EventFilters>) => {
    const next = { ...filters, ...patch }
    Object.keys(next).forEach((key) => {
      const k = key as keyof EventFilters
      if (next[k] === '' || next[k] === undefined) delete next[k]
    })
    onChange(next)
  }

  return (
    <div className={cn('rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3 text-xs space-y-2.5', className)}>
      <FiltersHeader
        title="Events Filter"
        activeCount={countActiveFilters(filters)}
        onReset={() => onChange({ limit: DEFAULT_HISTORY_PAGE_SIZE })}
      />

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          id="event-type-filter"
          label="Event Type"
          sizeVariant="sm"
          value={filters.event_type ?? ''}
          onChange={(e) => update({ event_type: e.target.value || undefined })}
          options={[{ value: '', label: 'All event types' }, ...EVENT_TYPES.map((value) => ({ value, label: value }))]}
        />

        <Select
          id="event-source-filter"
          label="Source"
          sizeVariant="sm"
          value={filters.source ?? ''}
          onChange={(event) => update({ source: event.target.value === 'admin_api' || event.target.value === 'scheduler' || event.target.value === 'observer' || event.target.value === 'cli' || event.target.value === 'control' ? event.target.value : undefined })}
          options={[{ value: '', label: 'All sources' }, ...(['admin_api', 'scheduler', 'observer', 'cli', 'control'] as const).map((value) => ({ value, label: value }))]}
        />
      </div>
    </div>
  )
}

function AuditFiltersBar({
  filters,
  onChange,
  accounts,
  exchanges,
  connectors,
  validationErrors = {},
  className,
}: Extract<HistoryFiltersBarProps, { mode: 'audit' }>) {
  const [manualActor, setManualActor] = useState(false)
  const update = (patch: Partial<AuditFilters>) => {
    const next = { ...filters, ...patch }
    Object.keys(next).forEach((key) => {
      const k = key as keyof AuditFilters
      if (next[k] === '' || next[k] === undefined) delete next[k]
    })
    onChange(next)
  }

  const applyPreset = (preset: QuickPresetId) => {
    if (preset === 'all') {
      update({ from_time: undefined, to_time: undefined })
      return
    }
    const now = new Date()
    let from: Date
    let to: Date = now
    if (preset === 'today') {
      from = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0)
      to = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999)
    } else {
      const presetDef = QUICK_PRESETS.find((p) => p.id === preset)
      if (!presetDef) return
      from = new Date(now.getTime() - presetDef.windowMs)
    }
    update({ from_time: from.toISOString(), to_time: to.toISOString() })
  }

  const actionOptions = useMemo(() => {
    if (filters.action && !AUDIT_ACTION_OPTIONS.some((opt) => opt.value === filters.action)) {
      return [...AUDIT_ACTION_OPTIONS, { value: filters.action, label: `${filters.action} (Other)` }]
    }
    return AUDIT_ACTION_OPTIONS
  }, [filters.action])

  const outcomeOptions = useMemo(() => {
    if (filters.outcome && !AUDIT_OUTCOME_OPTIONS.some((opt) => opt.value === filters.outcome)) {
      return [...AUDIT_OUTCOME_OPTIONS, { value: filters.outcome, label: `${filters.outcome} (Other)` }]
    }
    return AUDIT_OUTCOME_OPTIONS
  }, [filters.outcome])

  const environmentOptions = useMemo(() => {
    if (filters.environment && !AUDIT_ENVIRONMENT_OPTIONS.some((opt) => opt.value === filters.environment)) {
      return [...AUDIT_ENVIRONMENT_OPTIONS, { value: filters.environment, label: `${filters.environment} (Other)` }]
    }
    return AUDIT_ENVIRONMENT_OPTIONS
  }, [filters.environment])

  const exchangeOptions = useMemo(() => {
    const set = new Set(DEFAULT_EXCHANGES)
    if (exchanges) {
      exchanges.forEach((ex) => set.add(ex))
    }
    if (filters.exchange) {
      set.add(filters.exchange)
    }
    return [
      { value: '', label: 'All exchanges' },
      ...Array.from(set).map((ex) => ({
        value: ex,
        label: ex.toUpperCase() === 'OKX' ? 'OKX' : ex.charAt(0).toUpperCase() + ex.slice(1),
      })),
    ]
  }, [exchanges, filters.exchange])

  const connectorOptions = useMemo(() => {
    const map = new Map<string, string>()
    DEFAULT_CONNECTORS.forEach((d) => map.set(d.value, d.label))
    if (connectors) {
      connectors.forEach((c) => {
        if (!map.has(c)) map.set(c, c)
      })
    }
    if (filters.connector && !map.has(filters.connector)) {
      map.set(filters.connector, filters.connector)
    }
    return [
      { value: '', label: 'All connectors' },
      ...Array.from(map.entries()).map(([value, label]) => ({ value, label })),
    ]
  }, [connectors, filters.connector])

  const limitOptions = useMemo(() => {
    const currentStr = filters.limit ? String(filters.limit) : String(DEFAULT_HISTORY_PAGE_SIZE)
    if (!HISTORY_LIMIT_OPTIONS.some((opt) => opt.value === currentStr)) {
      return [...HISTORY_LIMIT_OPTIONS, { value: currentStr, label: `${currentStr} items` }]
    }
    return HISTORY_LIMIT_OPTIONS
  }, [filters.limit])

  const hasAccounts = Boolean(accounts && accounts.length > 0)
  const actorOptions = useMemo(() => {
    if (!hasAccounts) return []
    const opts = [
      { value: '', label: 'All actors' },
      ...(accounts ?? []).map((acc) => ({
        value: acc.id,
        label: `${acc.email} (${acc.role})`,
      })),
    ]
    if (filters.actor_id && !opts.some((opt) => opt.value === filters.actor_id)) {
      opts.push({ value: filters.actor_id, label: `ID: ${filters.actor_id}` })
    }
    return opts
  }, [accounts, filters.actor_id, hasAccounts])

  const timeRange = detectTimeRange(filters)
  // The default page size is not a user choice; only a non-default limit counts as an active advanced filter.
  const advancedActiveCount = AUDIT_ADVANCED_KEYS.filter((key) => {
    if (key === 'limit') return filters.limit !== undefined && filters.limit !== DEFAULT_HISTORY_PAGE_SIZE
    return filters[key] !== undefined && filters[key] !== ''
  }).length
  const [showAdvanced, setShowAdvanced] = useState(() => advancedActiveCount > 0)

  return (
    <div className={cn('rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3.5 text-xs space-y-3', className)}>
      <FiltersHeader
        title="Audit Filter"
        activeCount={countActiveFilters(filters)}
        onReset={() => onChange({ limit: DEFAULT_HISTORY_PAGE_SIZE })}
      />

      {/* Quick time range: segmented control with a persistent active state */}
      <div className="flex flex-wrap items-center gap-2 pb-1">
        <span className="text-[11px] font-medium text-app-muted flex items-center gap-1">
          <Clock size={12} />
          <span>Time range</span>
        </span>
        <div
          role="group"
          aria-label="Quick time range"
          className="inline-flex overflow-hidden rounded-button border border-app-border bg-app-surface"
        >
          {([
            { id: 'all', label: 'All time' },
            { id: '1h', label: '1h' },
            { id: '24h', label: '24h' },
            { id: 'today', label: 'Today' },
            { id: '7d', label: '7d' },
            { id: '30d', label: '30d' },
          ] as { id: QuickPresetId; label: string }[]).map((seg) => {
            const isActive = timeRange === seg.id
            return (
              <button
                key={seg.id}
                type="button"
                aria-pressed={isActive}
                onClick={() => applyPreset(seg.id)}
                className={cn(
                  'flex h-8 items-center border-r border-app-border px-3 text-[11px] font-medium transition-colors last:border-r-0',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action focus-visible:ring-inset',
                  isActive
                    ? 'bg-app-action text-white'
                    : 'text-app-muted hover:bg-app-surface-muted hover:text-app-text',
                )}
              >
                {seg.label}
              </button>
            )
          })}
        </div>
        {timeRange === 'custom' && (
          <Badge tone="neutral" className="px-2 py-0.5 text-[10px]">
            Custom range
          </Badge>
        )}
      </div>

      {/* Primary filters: always visible */}
      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <DateTimeInput
          id="audit-from-time"
          label="From time (UTC)"
          value={filters.from_time}
          onChange={(val) => update({ from_time: val })}
          error={validationErrors.from_time}
        />

        <DateTimeInput
          id="audit-to-time"
          label="To time (UTC)"
          value={filters.to_time}
          onChange={(val) => update({ to_time: val })}
          error={validationErrors.to_time}
        />

        <Select
          id="audit-action-filter"
          label="Action"
          sizeVariant="sm"
          value={filters.action ?? ''}
          onChange={(e) => update({ action: e.target.value || undefined })}
          options={actionOptions}
        />

        <Select
          id="audit-outcome-filter"
          label="Outcome"
          sizeVariant="sm"
          value={filters.outcome ?? ''}
          onChange={(e) => update({ outcome: e.target.value || undefined })}
          options={outcomeOptions}
        />

        {/* Actor filter: select when accounts exist, or free-text input; icon toggle switches modes */}
        {hasAccounts && !manualActor ? (
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <label htmlFor="audit-actor-select" className="font-semibold text-app-text text-xs">
                Actor
              </label>
              <button
                type="button"
                onClick={() => setManualActor(true)}
                aria-label="Switch to manual actor ID input"
                title="Enter actor ID manually"
                className="flex h-6 w-6 items-center justify-center rounded text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
              >
                <PenLine size={12} />
              </button>
            </div>
            <Select
              id="audit-actor-select"
              sizeVariant="sm"
              value={filters.actor_id ?? ''}
              onChange={(e) => update({ actor_id: e.target.value || undefined })}
              options={actorOptions}
            />
          </div>
        ) : (
          <div className="grid gap-1.5">
            <div className="flex items-center justify-between">
              <label htmlFor="audit-actor-input" className="font-semibold text-app-text text-xs">
                Actor
              </label>
              {hasAccounts && (
                <button
                  type="button"
                  onClick={() => setManualActor(false)}
                  aria-label="Switch to actor list select"
                  title="Select actor from list"
                  className="flex h-6 w-6 items-center justify-center rounded text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-action focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                >
                  <List size={12} />
                </button>
              )}
            </div>
            <Input
              id="audit-actor-input"
              sizeVariant="sm"
              placeholder="Filter by actor_id…"
              value={filters.actor_id ?? ''}
              onChange={(e) => update({ actor_id: e.target.value || undefined })}
            />
          </div>
        )}
      </div>

      {/* Advanced filters: collapsed unless one is set */}
      <div>
        <Button
          variant="ghost"
          aria-expanded={showAdvanced}
          onClick={() => setShowAdvanced((v) => !v)}
          className="h-8 px-2 text-[11px] gap-1.5"
        >
          {showAdvanced ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          <span>{showAdvanced ? 'Hide filters' : 'More filters'}</span>
          {advancedActiveCount > 0 && (
            <Badge tone="brand" className="px-1.5 py-0 text-[10px]">
              {advancedActiveCount}
            </Badge>
          )}
        </Button>
      </div>

      {showAdvanced && (
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
          <Select
            id="audit-exchange-filter"
            label="Exchange"
            sizeVariant="sm"
            value={filters.exchange ?? ''}
            onChange={(e) => update({ exchange: e.target.value || undefined })}
            options={exchangeOptions}
          />

          <Select
            id="audit-connector-filter"
            label="Connector"
            sizeVariant="sm"
            value={filters.connector ?? ''}
            onChange={(e) => update({ connector: e.target.value || undefined })}
            options={connectorOptions}
          />

          <Select
            id="audit-environment-filter"
            label="Environment"
            sizeVariant="sm"
            value={filters.environment ?? ''}
            onChange={(e) => update({ environment: e.target.value || undefined })}
            options={environmentOptions}
          />

          <Select
            id="audit-limit-filter"
            label="Limit"
            sizeVariant="sm"
            value={String(filters.limit ?? DEFAULT_HISTORY_PAGE_SIZE)}
            onChange={(e) => update({ limit: e.target.value ? Number(e.target.value) : undefined })}
            options={limitOptions}
            error={validationErrors.limit}
          />

          <Input
            id="audit-operation-filter"
            label="Operation ID"
            sizeVariant="sm"
            placeholder="Filter by operation_id…"
            value={filters.operation_id ?? ''}
            onChange={(e) => update({ operation_id: e.target.value || undefined })}
          />

          <Input
            id="audit-target-filter"
            label="Target ID"
            sizeVariant="sm"
            placeholder="Filter by target_id…"
            value={filters.target_id ?? ''}
            onChange={(event) => update({ target_id: event.target.value || undefined })}
          />
        </div>
      )}
    </div>
  )
}

export function HistoryFiltersBar(props: HistoryFiltersBarProps) {
  if (props.mode === 'commands') {
    return <CommandsFiltersBar {...props} />
  }
  if (props.mode === 'events') {
    return <EventsFiltersBar {...props} />
  }
  return <AuditFiltersBar {...props} />
}

import { useMemo, useState } from 'react'
import { Clock, Filter, RotateCcw } from 'lucide-react'
import type { AuditFilters, CommandFilters, EventFilters } from '../history/filters'
import type { components } from '../../core/api/schema'
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
      sessions?: Pick<components['schemas']['SessionSummaryOutput'], 'id' | 'profile_id' | 'status'>[]
      selectedSessionId?: string
      onSelectSession?: (id: string | undefined) => void
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
  { value: 'login', label: 'login — User login' },
  { value: 'access_denied', label: 'access_denied — Access denied' },
  { value: 'account_added', label: 'account_added — Account added' },
  { value: 'account_updated', label: 'account_updated — Account updated' },
  { value: 'account_bound', label: 'account_bound — Account bound' },
  // Credentials & API Keys
  { value: 'credentials_set', label: 'credentials_set — Exchange credentials set' },
  { value: 'credentials_disconnect', label: 'credentials_disconnect — Exchange disconnect' },
  { value: 'credentials_disconnect_progress', label: 'credentials_disconnect_progress — Disconnect progress' },
  { value: 'credentials_close_submitted', label: 'credentials_close_submitted — Close position order' },
  // Trading Session
  { value: 'trading_start', label: 'trading_start — Start trading session' },
  { value: 'trading_stop', label: 'trading_stop — Stop trading session' },
  { value: 'trading_switch', label: 'trading_switch — Switch trading session' },
  // Recovery
  { value: 'recovery_check_requested', label: 'recovery_check_requested — Recovery check requested' },
  { value: 'recovery_check_completed', label: 'recovery_check_completed — Recovery check completed' },
  { value: 'recovery_check_failed', label: 'recovery_check_failed — Recovery check failed' },
  { value: 'recovery_reset_requested', label: 'recovery_reset_requested — Recovery reset requested' },
  { value: 'recovery_reset_completed', label: 'recovery_reset_completed — Recovery reset completed' },
  { value: 'recovery_reset_failed', label: 'recovery_reset_failed — Recovery reset failed' },
  { value: 'recovery_position_repaired', label: 'recovery_position_repaired — Recovery position repaired' },
  { value: 'automatic_recovery_paused', label: 'automatic_recovery_paused — Automatic recovery paused' },
  { value: 'automatic_recovery_order_verified', label: 'automatic_recovery_order_verified — Recovery order verified' },
  { value: 'automatic_recovery_resumed', label: 'automatic_recovery_resumed — Automatic recovery resumed' },
  // Reconciliation
  { value: 'reconciliation_requested', label: 'reconciliation_requested — Reconciliation requested' },
]

const AUDIT_OUTCOME_OPTIONS = [
  { value: '', label: 'All outcomes' },
  { value: 'succeeded', label: 'Succeeded (succeeded)' },
  { value: 'success', label: 'Success (success)' },
  { value: 'failed', label: 'Failed (failed)' },
  { value: 'failure', label: 'Failure (failure)' },
  { value: 'denied', label: 'Denied (denied)' },
  { value: 'accepted', label: 'Accepted (accepted)' },
  { value: 'applying', label: 'Applying (applying)' },
]

const AUDIT_ENVIRONMENT_OPTIONS = [
  { value: '', label: 'All environments' },
  { value: 'live', label: 'Live (live)' },
  { value: 'demo', label: 'Demo / Paper (demo)' },
]

const DEFAULT_EXCHANGES = ['okx', 'bybit', 'binance']

const DEFAULT_CONNECTORS = [
  { value: 'spot', label: 'spot (Spot)' },
  { value: 'perpetual', label: 'perpetual (Perpetual Futures)' },
  { value: 'okx', label: 'okx' },
  { value: 'okx_perpetual', label: 'okx_perpetual' },
  { value: 'bybit', label: 'bybit' },
  { value: 'bybit_perpetual', label: 'bybit_perpetual' },
  { value: 'binance', label: 'binance' },
  { value: 'binance_perpetual', label: 'binance_perpetual' },
]

const AUDIT_LIMIT_OPTIONS = [
  { value: '25', label: '25 items' },
  { value: '50', label: '50 items' },
  { value: '100', label: '100 items (Default)' },
  { value: '200', label: '200 items' },
  { value: '500', label: '500 items (Maximum)' },
]

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
      <div className="flex items-center justify-between">
        <span className="font-semibold text-app-text flex items-center gap-1.5">
          <Filter size={13} className="text-app-action" />
          <span>Commands Filter</span>
        </span>
        <Button
          variant="secondary"
          onClick={() => onChange({})}
          className="h-6 px-2 text-[10px] gap-1"
        >
          <RotateCcw size={10} />
          <span>Reset filters</span>
        </Button>
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        <Select
          id="cmd-action-filter"
          label="Action"
          sizeVariant="sm"
          value={filters.action ?? ''}
          onChange={(e) => update({ action: (e.target.value as CommandFilters['action']) || undefined })}
          options={[
            { value: '', label: 'All actions' },
            { value: 'start', label: 'Start (start)' },
            { value: 'switch', label: 'Switch (switch)' },
            { value: 'stop', label: 'Stop (stop)' },
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
            { value: 'applying', label: 'Applying (applying)' },
            { value: 'accepted', label: 'Accepted (accepted)' },
            { value: 'succeeded', label: 'Succeeded (succeeded)' },
            { value: 'failed', label: 'Failed (failed)' },
            { value: 'superseded', label: 'Superseded (superseded)' },
          ]}
        />

        <Input
          label="Session ID"
          placeholder="Filter by session_id…"
          value={filters.session_id ?? ''}
          onChange={(e) => update({ session_id: e.target.value || undefined })}
        />

        <Input
          label="Limit"
          type="number"
          min={1}
          max={500}
          value={filters.limit ?? ''}
          onChange={(e) => update({ limit: e.target.value ? Number(e.target.value) : undefined })}
          error={validationErrors.limit}
        />
      </div>
    </div>
  )
}

function EventsFiltersBar({
  filters,
  onChange,
  sessions = [],
  selectedSessionId,
  onSelectSession,
  validationErrors = {},
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
      <div className="flex items-center justify-between">
        <span className="font-semibold text-app-text flex items-center gap-1.5">
          <Filter size={13} className="text-app-action" />
          <span>Events Filter</span>
        </span>
        <Button
          variant="secondary"
          onClick={() => onChange({})}
          className="h-6 px-2 text-[10px] gap-1"
        >
          <RotateCcw size={10} />
          <span>Reset filters</span>
        </Button>
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        {sessions.length > 0 && onSelectSession && (
          <Select
            id="event-session-filter"
            label="Select session to track"
            sizeVariant="sm"
            value={selectedSessionId ?? ''}
            onChange={(e) => onSelectSession(e.target.value || undefined)}
            options={[
              { value: '', label: 'Active / Latest Session' },
              ...sessions.map((s) => ({ value: s.id, label: `${s.profile_id} (${s.id.slice(0, 8)}…)` })),
            ]}
          />
        )}

        <Input
          label="Event Type"
          placeholder="e.g. state_changed, step_expired…"
          value={filters.event_type ?? ''}
          onChange={(e) => update({ event_type: e.target.value || undefined })}
        />

        <Select
          id="event-source-filter"
          label="Source"
          sizeVariant="sm"
          value={filters.source ?? ''}
          onChange={(event) => update({ source: event.target.value === 'admin_api' || event.target.value === 'scheduler' || event.target.value === 'observer' || event.target.value === 'cli' || event.target.value === 'control' ? event.target.value : undefined })}
          options={[{ value: '', label: 'All sources' }, ...(['admin_api', 'scheduler', 'observer', 'cli', 'control'] as const).map((value) => ({ value, label: value }))]}
        />

        <Input
          label="Limit"
          type="number"
          min={1}
          max={500}
          value={filters.limit ?? ''}
          onChange={(e) => update({ limit: e.target.value ? Number(e.target.value) : undefined })}
          error={validationErrors.limit}
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

  const applyPreset = (preset: '1h' | '24h' | 'today' | '7d' | '30d') => {
    const now = new Date()
    let from: Date
    let to: Date = now
    if (preset === '1h') {
      from = new Date(now.getTime() - 60 * 60 * 1000)
    } else if (preset === '24h') {
      from = new Date(now.getTime() - 24 * 60 * 60 * 1000)
    } else if (preset === 'today') {
      from = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 0, 0, 0, 0)
      to = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59, 999)
    } else if (preset === '7d') {
      from = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000)
    } else {
      from = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000)
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
    const currentStr = filters.limit ? String(filters.limit) : '100'
    if (!AUDIT_LIMIT_OPTIONS.some((opt) => opt.value === currentStr)) {
      return [...AUDIT_LIMIT_OPTIONS, { value: currentStr, label: `${currentStr} items` }]
    }
    return AUDIT_LIMIT_OPTIONS
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

  return (
    <div className={cn('rounded-card border border-app-border/70 bg-app-surface-muted/30 p-3.5 text-xs space-y-3', className)}>
      <div className="flex items-center justify-between border-b border-app-border/50 pb-2">
        <span className="font-semibold text-app-text flex items-center gap-1.5">
          <Filter size={13} className="text-app-action" />
          <span>Audit Filter</span>
        </span>
        <Button
          variant="secondary"
          onClick={() => onChange({})}
          className="h-6 px-2 text-[10px] gap-1"
        >
          <RotateCcw size={10} />
          <span>Reset filters</span>
        </Button>
      </div>

      {/* Date/Time Quick Presets */}
      <div className="flex flex-wrap items-center gap-1.5 pb-1">
        <span className="text-[11px] font-medium text-app-muted flex items-center gap-1 mr-1">
          <Clock size={12} />
          <span>Quick range:</span>
        </span>
        <button
          type="button"
          onClick={() => applyPreset('1h')}
          className="rounded-button border border-app-border bg-app-surface px-2 py-0.5 text-[11px] text-app-muted hover:border-app-action/50 hover:text-app-text transition-colors"
        >
          Last 1h
        </button>
        <button
          type="button"
          onClick={() => applyPreset('24h')}
          className="rounded-button border border-app-border bg-app-surface px-2 py-0.5 text-[11px] text-app-muted hover:border-app-action/50 hover:text-app-text transition-colors"
        >
          Last 24h
        </button>
        <button
          type="button"
          onClick={() => applyPreset('today')}
          className="rounded-button border border-app-border bg-app-surface px-2 py-0.5 text-[11px] text-app-muted hover:border-app-action/50 hover:text-app-text transition-colors"
        >
          Today
        </button>
        <button
          type="button"
          onClick={() => applyPreset('7d')}
          className="rounded-button border border-app-border bg-app-surface px-2 py-0.5 text-[11px] text-app-muted hover:border-app-action/50 hover:text-app-text transition-colors"
        >
          Last 7d
        </button>
        <button
          type="button"
          onClick={() => applyPreset('30d')}
          className="rounded-button border border-app-border bg-app-surface px-2 py-0.5 text-[11px] text-app-muted hover:border-app-action/50 hover:text-app-text transition-colors"
        >
          Last 30d
        </button>
        {(filters.from_time || filters.to_time) && (
          <button
            type="button"
            onClick={() => update({ from_time: undefined, to_time: undefined })}
            className="ml-auto text-[11px] text-app-muted hover:text-app-action underline decoration-dotted"
          >
            Clear time range
          </button>
        )}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
        {/* Date/Time pickers */}
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

        {/* Action Select */}
        <Select
          id="audit-action-filter"
          label="Action"
          sizeVariant="sm"
          value={filters.action ?? ''}
          onChange={(e) => update({ action: e.target.value || undefined })}
          options={actionOptions}
        />

        {/* Outcome Select */}
        <Select
          id="audit-outcome-filter"
          label="Outcome"
          sizeVariant="sm"
          value={filters.outcome ?? ''}
          onChange={(e) => update({ outcome: e.target.value || undefined })}
          options={outcomeOptions}
        />

        {/* Environment Select */}
        <Select
          id="audit-environment-filter"
          label="Environment"
          sizeVariant="sm"
          value={filters.environment ?? ''}
          onChange={(e) => update({ environment: e.target.value || undefined })}
          options={environmentOptions}
        />

        {/* Exchange Select */}
        <Select
          id="audit-exchange-filter"
          label="Exchange"
          sizeVariant="sm"
          value={filters.exchange ?? ''}
          onChange={(e) => update({ exchange: e.target.value || undefined })}
          options={exchangeOptions}
        />

        {/* Connector Select */}
        <Select
          id="audit-connector-filter"
          label="Connector"
          sizeVariant="sm"
          value={filters.connector ?? ''}
          onChange={(e) => update({ connector: e.target.value || undefined })}
          options={connectorOptions}
        />

        {/* Limit Select */}
        <Select
          id="audit-limit-filter"
          label="Limit"
          sizeVariant="sm"
          value={filters.limit ? String(filters.limit) : '100'}
          onChange={(e) => update({ limit: e.target.value ? Number(e.target.value) : undefined })}
          options={limitOptions}
          error={validationErrors.limit}
        />

        {/* Actor Filter: Select when accounts exist, or Input */}
        {hasAccounts && !manualActor ? (
          <div className="relative">
            <Select
              id="audit-actor-select"
              label="Actor ID (actor_id)"
              sizeVariant="sm"
              value={filters.actor_id ?? ''}
              onChange={(e) => update({ actor_id: e.target.value || undefined })}
              options={actorOptions}
            />
            <button
              type="button"
              onClick={() => setManualActor(true)}
              className="absolute right-0 top-0 text-[10px] text-app-muted hover:text-app-action underline decoration-dotted"
            >
              Enter ID manually
            </button>
          </div>
        ) : (
          <div className="relative">
            <Input
              id="audit-actor-input"
              label="Actor ID (actor_id)"
              placeholder="Filter by actor_id…"
              value={filters.actor_id ?? ''}
              onChange={(e) => update({ actor_id: e.target.value || undefined })}
            />
            {hasAccounts && (
              <button
                type="button"
                onClick={() => setManualActor(false)}
                className="absolute right-0 top-0 text-[10px] text-app-muted hover:text-app-action underline decoration-dotted"
              >
                Select from list
              </button>
            )}
          </div>
        )}

        {/* Operation ID Input */}
        <Input
          id="audit-operation-filter"
          label="Operation ID (operation_id)"
          placeholder="Filter by operation_id…"
          value={filters.operation_id ?? ''}
          onChange={(e) => update({ operation_id: e.target.value || undefined })}
        />

        {/* Target ID Input */}
        <Input
          id="audit-target-filter"
          label="Target ID (target_id)"
          placeholder="Filter by target_id…"
          value={filters.target_id ?? ''}
          onChange={(event) => update({ target_id: event.target.value || undefined })}
        />
      </div>
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

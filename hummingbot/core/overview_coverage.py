"""Bounded second buckets for reporting only; no order admission calls."""
import math

WINDOW = 1800
GROUPS = ('balance', 'inventory', 'bootstrap', 'guard', 'policy')
RESTRICTIONS = {
    'insufficient_quote_balance': 'balance', 'insufficient_base_balance': 'balance',
    'reduction_fee_balance_unavailable': 'balance', 'inventory_limit': 'inventory',
    'inventory_skew_sell_suppressed': 'inventory',
    'spot_outflow_blocked': 'policy', 'volatility_paused': 'guard',
    'reference_deviation': 'guard', 'reference_out_of_bounds': 'guard',
    'quote_out_of_bounds': 'guard', 'sideways_breakout': 'guard',
    'guard_recovering': 'guard',
    'spread_policy_rejected': 'guard', 'quote_price_rejected': 'guard',
    'reduction_price_not_profitable': 'guard', 'reduction_price_rejected': 'guard',
    'bootstrap_not_started': 'bootstrap', 'bootstrap_expired': 'bootstrap',
    'bootstrap_band': 'bootstrap', 'bootstrap_range': 'bootstrap',
    'bootstrap_quote_crossing': 'bootstrap',
}
NORMAL_WAITS = {'active_executor', 'cooldown', 'proposal_pending_confirmation', 'allocation_limit', 'reduction_reserved',
                'inventory_skew_sell_below_minimum'}


def eligibility(observation, *, now, running):
    """Fresh resident proposal/order evidence, never readiness alone."""
    if not running or not observation.get('valid') or observation.get('slo_scope_valid') is not True:
        return 'unknown'
    proposal = observation.get('proposal')
    if not isinstance(proposal, dict):
        return 'unknown'
    stamp = proposal.get('observed_at')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now - stamp <= 5:
        return 'unknown'
    reasons = proposal.get('reasons')
    if not isinstance(reasons, dict):
        return 'unknown'
    codes = set()
    for side in ('BUY', 'SELL'):
        raw = reasons.get(side)
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            return 'unknown'
        codes.update(raw)
    readiness = str(observation.get('reason_code') or '').split(':', 1)[0]
    if readiness:
        codes.add(readiness)
    # Missing valuation/private/balance evidence must not be excused by a guard.
    if codes - set(RESTRICTIONS) - NORMAL_WAITS:
        return 'unknown'
    for group in GROUPS:
        if any(RESTRICTIONS.get(code) == group for code in codes):
            return group
    if not observation.get('eligible') or observation.get('session_admitted') is not True:
        return 'unknown'
    for side, ids in (('BUY', 'bid_ids'), ('SELL', 'ask_ids')):
        pending = (observation.get('pending_orders') or {}).get(side) or {}
        proposed = (proposal.get('proposed') or {}).get(side, 0)
        funded = (bool(observation.get(ids)) or isinstance(proposed, int) and proposed > 0
                  or any(isinstance(pending.get(key), int) and pending[key] > 0
                         for key in ('submission_pending', 'cancel_confirmation')))
        if not funded:
            return 'unknown'
    return 'eligible'


def add_interval(buckets, *, start, end, state, two_sided):
    """At most 1,801 buckets independent of sample frequency/state churn.

    Layout: first/last time, observed, two-sided, eligible, eligible two-sided,
    then restriction seconds in GROUPS order. Partial rolling-edge buckets are
    left unknown, because fractional distribution cannot be reconstructed.
    """
    while start < end:
        second = math.floor(start)
        stop = min(end, second + 1)
        duration = stop - start
        key = str(second)
        row = buckets.setdefault(key, [start, stop, 0., 0., 0., 0.] + [0.] * len(GROUPS))
        row[1] = max(row[1], stop)
        row[2] += duration
        if two_sided:
            row[3] += duration
        if state == 'eligible':
            row[4] += duration
            if two_sided:
                row[5] += duration
        elif state in GROUPS:
            row[6 + GROUPS.index(state)] += duration
        start = stop


def project_buckets(buckets, *, start, end):
    """Validate private checkpoints and aggregate once, rather than once per minute."""
    if not isinstance(buckets, dict) or len(buckets) > WINDOW + 1:
        return None
    totals = [0.] * (4 + len(GROUPS))
    minutes = {}
    for key, row in buckets.items():
        if (not isinstance(key, str) or not key.lstrip('-').isdigit() or not isinstance(row, list)
                or len(row) != 6 + len(GROUPS)
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in row)):
            return None
        second = int(key)
        if str(second) != key:
            return None
        first, last, observed, two, eligible, eligible_two, *restricted = row
        if (not second <= first < last <= second + 1 or any(value < 0 for value in row[2:])
                or observed > last - first + 1e-7 or two > observed + 1e-7
                or eligible_two > min(two, eligible) + 1e-7
                or eligible + sum(restricted) > observed + 1e-7):
            return None
        if first < start or last > end:
            continue
        values = [observed, two, eligible, eligible_two, *restricted]
        minute = second // 60 * 60
        target = minutes.setdefault(minute, [0.] * len(totals))
        for index, value in enumerate(values):
            totals[index] += value
            target[index] += value
    return totals, minutes


def eligibility_fields(values, duration, *, full_window):
    observed, two, eligible, eligible_two, *groups = values
    restricted = sum(groups)
    unknown = max(0., duration - eligible - restricted)
    denominator = eligible + unknown
    return dict(eligible_seconds=eligible, two_sided_eligible_seconds=eligible_two,
                restricted_seconds=restricted, restriction_seconds=dict(zip(GROUPS, groups)),
                eligibility_unknown_seconds=unknown,
                conditional_eligible_fraction=min(1., eligible_two / eligible) if eligible > 0 else None,
                conservative_eligible_fraction=min(1., eligible_two / denominator) if eligible > 0 and denominator > 0 else None,
                measurement_ready=full_window and eligible > 0,
                measurement_reason=None if full_window and eligible > 0 else 'partial_window' if not full_window else 'no_eligible_time')

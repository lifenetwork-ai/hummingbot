"""Operator presentation of sampled quotes and the last actual proposal evaluation."""
import math
import re

from hummingbot.core.mm_readiness import MM_READINESS_REASONS

REASONS = {
    'insufficient_quote_balance': 'Available USDT does not cover the proposed order. Check balances and open-order reservations.',
    'insufficient_base_balance': 'Available token balance does not cover the proposed sell. Check balances and open-order reservations.',
    'inventory_limit': 'The configured exposure or inventory limit blocks new orders. Review exposure and managed limits.',
    'spot_outflow_blocked': 'The spending guard did not approve this BUY. Check the spending-policy status.',
    'allocation_limit': 'The configured quote allocation is already reserved.',
    'order_size_rejected': 'The rounded order did not pass size or allocation checks.',
    'quote_price_rejected': 'The proposed price does not pass the configured price checks.',
    'spread_policy_rejected': 'The proposed order does not pass the configured spread checks.',
    'spread_policy_unavailable': 'Required spread information is unavailable.',
    'volatility_paused': 'Volatility exceeded the configured limit. Wait for fresh data and automatic recovery; do not restart to bypass it.',
    'volatility_checkpoint_pending': 'Recovery state is awaiting durable confirmation. Wait; contact support if this persists.',
    'candles_unavailable': 'Recent completed candles are unavailable. The configured fallback or wait policy applies.',
    'active_executor': 'An existing order task is handling this price level.',
    'cooldown': 'The configured cooldown is still active.',
    'account_balance_unavailable': 'Available account balances cannot be confirmed.',
    'proposal_evaluation_unavailable': 'The latest proposal could not be evaluated.',
    'session_not_admitted': 'The session is waiting for trading admission.',
    'market_data_unavailable': 'Required market data is unavailable.',
    'controller_unavailable': 'The controller needs technical review.',
    'readiness_unavailable': 'Required trading conditions are not ready.',
    'quote_reason_unavailable': 'No recent proposal reason is available. Check order and service details.',
    'proposal_pending_confirmation': 'A proposed order is awaiting runtime or exchange confirmation.',
}


def wait_diagnosis(observation, side, codes, *, proposal, fresh, previous, delta, continuous, warning_seconds):
    """Describe local evidence, never transport admission or a guessed root cause."""
    pending = (observation.get('pending_orders') or {}).get(side, {})
    guidance = {
        'cancel_confirmation': 'Wait for cancellation confirmation; contact support if it persists.',
        'submission_pending': 'Wait for order confirmation. A pending order may still be waiting to be sent.',
        'executor_report': 'Wait for order processing to report progress; check service status if it persists.',
        'executor_activity': 'Check the existing order task; contact support if progress does not resume.',
        'cooldown': 'Wait for the configured cooldown to finish.',
        'proposal_confirmation': 'Check orders and service status; a suggested order may not yet be accepted.',
        'unknown': 'Check service status and order details; contact support if the reason is still unavailable.',
    }
    if pending.get('cancel_confirmation', 0) > 0:
        stage, source = 'cancel_confirmation', 'order_tracker'
    elif pending.get('submission_pending', 0) > 0:
        stage, source = 'submission_pending', 'order_tracker'
    elif set(codes) - {'active_executor', 'cooldown', 'quote_reason_unavailable', 'proposal_pending_confirmation'}:
        stage, source = 'trading_conditions', 'controller_observation'
    elif observation.get('awaiting_executor_report') is True:
        stage, source = 'executor_report', 'executor_event'
    elif 'cooldown' in codes:
        stage, source = 'cooldown', 'controller_observation'
    elif 'active_executor' in codes:
        stage, source = 'executor_activity', 'controller_observation'
    elif 'proposal_pending_confirmation' in codes:
        stage, source = 'proposal_confirmation', 'controller_observation'
    else:
        stage, source = 'unknown', 'unavailable'
    prior = (previous or {}).get('diagnosis') or {}
    seconds = prior.get('observed_seconds', 0)
    same = prior.get('stage') == stage and (previous or {}).get('reason_codes') == codes
    seconds = (seconds + delta if continuous and 0 < delta <= 5 and same
               and isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
               and math.isfinite(seconds) and seconds >= 0 else 0)
    if not math.isfinite(seconds):
        seconds = 0
    if stage == 'trading_conditions':
        if set(codes) & {'insufficient_quote_balance', 'insufficient_base_balance', 'account_balance_unavailable'}:
            action = 'Check available balances and open-order reservations.'
        elif set(codes) & {'inventory_limit', 'allocation_limit', 'spot_outflow_blocked'}:
            action = 'Review exposure and configured limits; do not reset protective state to bypass them.'
        else:
            action = 'Check the reported trading condition and service incidents; contact support if unresolved.'
    else:
        action = guidance[stage]
    if stage not in ('trading_conditions', 'cooldown') and warning_seconds > 0 and seconds >= warning_seconds:
        action = 'Contact support with service incidents and order details; this observed wait exceeded the warning interval.'
    error = proposal.get('error_type') if fresh and 'proposal_evaluation_unavailable' in codes else None
    error = error if isinstance(error, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,79}', error) else None
    return dict(stage=stage, source=source, observed_seconds=seconds, next_action=action, error_type=error)


def unavailable_activity(warning_seconds, *, observed_at=None):
    return dict(state='unavailable', label='Order status unavailable', detail='Current working orders cannot be confirmed.',
                fill_observation='unavailable', fill_observation_basis='current_working_orders',
                no_quote_seconds=None, warning_after_seconds=warning_seconds, warning_active=False,
                proposal_observed_at=observed_at,
                sides={side: dict(state='unavailable', order_count=None, proposed_count=None,
                                  reason_codes=['quote_observation_unavailable'], detail='Current working orders cannot be confirmed.',
                                  diagnosis=None)
                       for side in ('BUY', 'SELL')})


def quote_activity(observation, *, now, previous, delta, continuous, monitoring, warning_seconds):
    proposal = observation.get('proposal')
    proposal = proposal if isinstance(proposal, dict) else {}
    stamp = proposal.get('observed_at')
    fresh = isinstance(stamp, (float, int)) and math.isfinite(stamp) and 0 <= now - stamp <= 5
    if not observation.get('valid'):
        return unavailable_activity(warning_seconds, observed_at=stamp if fresh else None)
    counts = {'BUY': observation.get('buy_order_count', len(observation.get('bid_ids', []))),
              'SELL': observation.get('sell_order_count', len(observation.get('ask_ids', [])))}
    sides = {}
    for side, count in counts.items():
        codes = (proposal.get('reasons', {}).get(side) or []) if fresh else []
        readiness = str(observation.get('reason_code') or '').split(':', 1)[0]
        if not observation.get('eligible') and readiness in MM_READINESS_REASONS:
            codes = [readiness]
        codes = list(dict.fromkeys(code for code in codes if code in REASONS or code in MM_READINESS_REASONS))
        proposed = proposal.get('proposed', {}).get(side, 0) if fresh else None
        if count:
            state, detail = 'working', 'Acknowledged working orders are present.'
        elif not monitoring:
            state, detail = 'idle', 'The session is not actively quoting.'
        else:
            if not codes:
                codes = ['proposal_pending_confirmation' if proposed else 'quote_reason_unavailable']
            state = 'pending' if set(codes) <= {'active_executor', 'cooldown', 'proposal_pending_confirmation'} else 'blocked'
            detail = ' '.join(REASONS.get(code, 'Waiting for required market data or guard conditions.') for code in codes)
        diagnosis = None
        if monitoring and not count:
            previous_side = ((previous or {}).get('quote_activity') or {}).get('sides', {}).get(side)
            diagnosis = wait_diagnosis(observation, side, codes, proposal=proposal, fresh=fresh,
                                       previous=previous_side, delta=delta, continuous=continuous,
                                       warning_seconds=warning_seconds)
        sides[side] = dict(state=state, order_count=count, proposed_count=proposed, reason_codes=codes, detail=detail,
                           diagnosis=diagnosis)
    working = sum(counts.values())
    prior = (previous or {}).get('quote_activity') or {}
    duration = (prior.get('no_quote_seconds') or 0) + delta if continuous and prior.get('state') == 'no_quotes' else 0
    duration = duration if monitoring and not working else 0
    warning = monitoring and not working and warning_seconds > 0 and duration >= warning_seconds
    fills = observation.get('working_orders_with_fills')
    fill_state = ('unavailable' if fills is None else 'fills_on_working_orders' if fills else
                  'no_fills_on_working_orders') if working else 'not_applicable'
    if not monitoring:
        state, label, detail = 'idle', 'Session is not placing orders', 'Check outstanding orders and positions separately.'
    elif not working:
        state, label, detail = 'no_quotes', 'No working orders observed', 'Check the BUY and SELL reasons for this market.'
    elif all(counts.values()):
        state, label, detail = 'orders_working', 'Orders working', 'Working buy and sell orders are present.'
    else:
        state, label, detail = 'one_sided', 'Orders working on one side', 'Check the side without working orders.'
    if working and fill_state == 'no_fills_on_working_orders':
        detail += ' Current working orders have no observed fills.'
    return dict(state=state, label=label, detail=detail, sides=sides,
                fill_observation=fill_state, fill_observation_basis='current_working_orders',
                no_quote_seconds=duration, warning_after_seconds=warning_seconds, warning_active=bool(warning),
                proposal_observed_at=stamp if isinstance(stamp, (float, int)) and math.isfinite(stamp) else None)

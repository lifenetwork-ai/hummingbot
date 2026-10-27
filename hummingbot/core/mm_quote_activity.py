"""Operator presentation of sampled quotes and the last actual proposal evaluation."""
import math

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


def unavailable_activity(warning_seconds, *, observed_at=None):
    return dict(state='unavailable', label='Order status unavailable', detail='Current working orders cannot be confirmed.',
                fill_observation='unavailable', fill_observation_basis='current_working_orders',
                no_quote_seconds=None, warning_after_seconds=warning_seconds, warning_active=False,
                proposal_observed_at=observed_at,
                sides={side: dict(state='unavailable', order_count=None, proposed_count=None,
                                  reason_codes=['quote_observation_unavailable'], detail='Current working orders cannot be confirmed.')
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
        sides[side] = dict(state=state, order_count=count, proposed_count=proposed, reason_codes=codes, detail=detail)
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

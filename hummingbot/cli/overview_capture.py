"""Optional owner-loop Overview sampling over resident state only."""
import math
import time
from itertools import islice

from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.core.mm_liquidity import rolling_coverage
from hummingbot.core.overview_market_observations import capture_overview_markets
from hummingbot.core.overview_recovery import PostFillRecoveryObserver


def _unknown(reason, now):
    return dict(value=None, observed_at=now, stale=True, unavailable_reason=reason)


def _recovery_input(controller, session, private):
    """Only cached executor configs prove order ownership for the current step."""
    config = controller.config
    provider = controller.market_data_provider
    connector = provider.connectors.get(config.connector_name)
    if connector is None:
        return dict(valid=False)
    infos = list(islice(iter(getattr(controller, 'executors_info', [])), 2001))
    if len(infos) > 2000:
        return dict(valid=False)
    order_ids = set()
    for info in infos:
        owned = info.config
        if (getattr(owned, 'mm_session_id', None), getattr(owned, 'mm_generation', None)) != (session.id, session.generation):
            continue
        custom = info.custom_info
        identities = custom.get('order_ids') if isinstance(custom, dict) else None
        if isinstance(identities, list):
            order_ids.update(identity for identity in identities if isinstance(identity, str) and identity)
    events = list(islice(iter(getattr(connector, 'event_logs', [])), 1051))
    if len(events) > 1050:
        return dict(valid=False)
    fills = [dict(timestamp=event.timestamp, order_id=event.order_id, exchange_trade_id=event.exchange_trade_id)
             for event in events if isinstance(event, OrderFilledEvent) and event.trading_pair == config.trading_pair]
    return dict(valid=private.get('valid') is True and getattr(session, 'status', None) == 'running',
                two_sided=private.get('two_sided') is True, order_ids=order_ids, events=fills)


def capture_overview(host, *, now=None, monotonic_now=None):
    now = time.time() if now is None else now
    monotonic_now = time.monotonic() if monotonic_now is None else monotonic_now
    result = capture_overview_markets(host, now=now)
    session = getattr(host, 'mm_session', None)
    if session is None:
        return result
    frame = getattr(host, 'mm_liquidity_state', None)
    synchronized = (isinstance(frame, dict) and frame.get('session_id') == session.id
                    and frame.get('generation') == session.generation
                    and isinstance(frame.get('observed_at'), (int, float))
                    and math.isfinite(frame['observed_at']) and 0 <= now - frame['observed_at'] <= 5)
    observations = {}
    controllers = {('spot' if item.config.connector_name == 'okx' else 'perpetual'): item for item in host._mm_controllers()}
    for market in result['markets']:
        name = market['market']
        private = (frame.get('markets') or {}).get(name) or {} if synchronized else {}
        if not private.get('valid'):
            market['depth'] = _unknown('private_observation_unavailable', now)
            market['slippage_estimate'] = _unknown('private_observation_unavailable', now)
        if synchronized:
            try:
                market['coverage'] = dict(value=rolling_coverage(frame, name, now=now), observed_at=now,
                                          stale=False, unavailable_reason=None)
            except (ValueError, TypeError, ArithmeticError):
                market['coverage'] = _unknown('rolling_coverage_history_unavailable', now)
        try:
            observations[name] = _recovery_input(controllers[name], session, private)
        except (AttributeError, ValueError, TypeError, KeyError):
            observations[name] = dict(valid=False)
    observer = getattr(host, 'overview_recovery_observer', None)
    if not isinstance(observer, PostFillRecoveryObserver):
        observer = PostFillRecoveryObserver(frame.get('_overview_recovery') if isinstance(frame, dict) else None)
        host.overview_recovery_observer = observer
    recovery = observer.observe(dict(id=session.id, generation=session.generation), observations,
                                now=now, monotonic_now=monotonic_now, owner_generation=host.owner.generation)
    for market in result['markets']:
        market['post_fill_recovery'] = recovery[market['market']]
    if synchronized:
        frame['_overview_recovery'] = observer.checkpoint
        liquidity = getattr(host, 'liquidity_observer', None)
        if liquidity is not None and isinstance(liquidity.frame, dict):
            liquidity.frame['_overview_recovery'] = observer.checkpoint
    return result

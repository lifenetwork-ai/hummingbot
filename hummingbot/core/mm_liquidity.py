"""Sampled own-quote coverage, reusing tracker/status data with no exchange IO."""
import math
import time
from copy import deepcopy
from decimal import Decimal
from itertools import islice

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState


def own_quotes(connector, pair, *, max_orders):
    orders = list(islice(connector.in_flight_orders.values(), max_orders + 1))
    if len(orders) > max_orders:
        raise ValueError("liquidity_order_limit")
    sides = {TradeType.BUY: [], TradeType.SELL: []}
    for order in orders:
        if order.trading_pair != pair or not order.order_type.is_limit_type() or not order.exchange_order_id or order.current_state not in (OrderState.OPEN, OrderState.PARTIALLY_FILLED):
            continue
        price, amount, filled = (Decimal(str(value)) for value in (order.price, order.amount, order.executed_amount_base))
        if any(not value.is_finite() for value in (price, amount, filled)) or price <= 0 or not 0 <= filled <= amount:
            raise ValueError("liquidity_order_unavailable")
        if amount > filled:
            sides[order.trade_type].append((price, amount - filled, order.client_order_id))
    bids, asks = sides[TradeType.BUY], sides[TradeType.SELL]
    bid = max((item[0] for item in bids), default=None)
    ask = min((item[0] for item in asks), default=None)
    if bid is not None and ask is not None and bid >= ask:
        raise ValueError("liquidity_own_quotes_crossed")
    return dict(best_bid=bid, best_ask=ask, bid_notional=sum((p * a for p, a, _ in bids), Decimal('0')),
                ask_notional=sum((p * a for p, a, _ in asks), Decimal('0')),
                spread_bps=(ask - bid) / ((ask + bid) / 2) * 10000 if bid is not None and ask is not None else None,
                two_sided=bool(bids and asks), bid_ids=[item[2] for item in bids], ask_ids=[item[2] for item in asks])


class LiquidityObserver:
    def __init__(self, checkpoint=None):
        self.frame = deepcopy(checkpoint) if isinstance(checkpoint, dict) else None
        self.previous = None
        self.previous_monotonic = None
        self.previous_owner = None

    def observe(self, session, observations, *, now, monotonic_now, owner_generation):
        if not math.isfinite(now) or not math.isfinite(monotonic_now):
            raise ValueError("liquidity_clock_unavailable")
        started = session.get('started_at')
        ended = (self.frame or {}).get('ended_at') if (self.frame or {}).get('session_id') == session['session_id'] else None
        if session.get('status') in ('stopping', 'stopped', 'expired') and ended is None:
            ended = min(now, session.get('expires_at') or now) if session.get('status') == 'expired' else now
        elapsed = max(0, (ended if ended is not None else now) - started) if started is not None and math.isfinite(started) else 0
        if not self.frame or self.frame.get('session_id') != session['session_id']:
            self.frame = dict(session_id=session['session_id'], coverage_started_at=now, markets={})
            self.previous = None
        old = self.frame.get('markets', {})
        if not isinstance(old, dict) or not set(old).union(observations) <= {'spot', 'perpetual'}:
            raise ValueError('liquidity_checkpoint_unavailable')
        current = {}
        delta = monotonic_now - self.previous_monotonic if self.previous_monotonic is not None else 0
        continuous = (self.previous is not None and self.previous_owner == owner_generation
                      and ended is None and self.frame.get('generation') == session['generation'] and 0 < delta <= 5)
        for market in set(old).union(observations):
            observation = observations.get(market, dict(valid=False, eligible=False, reason_code='liquidity_observation_unavailable',
                                                        best_bid=None, best_ask=None, bid_notional=None, ask_notional=None, spread_bps=None, two_sided=False,
                                                        connector=old.get(market, {}).get('connector'), pair=old.get(market, {}).get('pair')))
            prior = old.get(market, {})
            totals = {key: float(prior.get(key, 0)) for key in ('observed_seconds', 'eligible_seconds', 'two_sided_seconds')}
            if any(not math.isfinite(value) or value < 0 or value > elapsed for value in totals.values()):
                raise ValueError("liquidity_checkpoint_unavailable")
            before = self.previous.get(market) if self.previous else None
            if continuous and before and before.get('valid') and observation.get('valid'):
                totals['observed_seconds'] += delta
                if before.get('eligible') and observation.get('eligible'):
                    totals['eligible_seconds'] += delta
                persistent = (set(before.get('bid_ids', [])).intersection(observation.get('bid_ids', []))
                              and set(before.get('ask_ids', [])).intersection(observation.get('ask_ids', [])))
                if persistent:
                    totals['two_sided_seconds'] += delta
            totals = {key: min(value, elapsed) for key, value in totals.items()}
            current[market] = {**{key: value for key, value in observation.items() if key not in ('bid_ids', 'ask_ids')},
                               **totals, 'elapsed_seconds': elapsed, 'unknown_seconds': max(0, elapsed - totals['observed_seconds']),
                               'two_sided_fraction_of_elapsed': totals['two_sided_seconds'] / elapsed if elapsed else None}
        self.frame.update(generation=session['generation'], profile_id=session['profile_id'], started_at=started,
                          observed_at=now, ended_at=ended, markets=current, measurement='sampled_owner_observations')
        self.previous = deepcopy(observations)
        self.previous_monotonic = monotonic_now
        self.previous_owner = owner_generation
        return deepcopy(self.frame)


def capture_liquidity(host):
    """Optional observer: a broken projection cannot revoke trading intent."""
    session = host.mm_session
    if session is None:
        return None
    observer = getattr(host, 'liquidity_observer', None)
    if not isinstance(observer, LiquidityObserver):
        row = host.owner.store.read()
        observer = host.liquidity_observer = LiquidityObserver((row.get('status') or {}).get('mm_liquidity'))
    observations = {}
    for controller in host._mm_controllers():
        name = controller.config.connector_name
        market = 'spot' if name == 'okx' else 'perpetual'
        value = dict(valid=False, eligible=False, reason_code='liquidity_observation_unavailable',
                     best_bid=None, best_ask=None, bid_notional=None, ask_notional=None, spread_bps=None, two_sided=False,
                     connector=name, pair=controller.config.trading_pair, bid_ids=[], ask_ids=[])
        try:
            provider = controller.market_data_provider
            connector = provider.get_connector_with_fallback(name)
            now = provider.time()
            book = provider.get_order_book(name, controller.config.trading_pair)
            book_at = max(book.snapshot_uid, book.last_diff_uid) / (1000 if name == 'okx_perpetual' else 1)
            private_at = connector._user_stream_tracker.last_recv_time
            if (not provider.ready or not -1 <= now - book_at <= float(controller.config.max_data_age_seconds)
                    or not -1 <= now - private_at <= connector.TICK_INTERVAL_LIMIT):
                raise ValueError('liquidity_observation_unavailable')
            value.update(own_quotes(connector, controller.config.trading_pair, max_orders=controller.config.quote_guards.max_own_orders))
            value.update(valid=True, eligible=host.phase == 'running' and bool(controller.processed_data.get('ready')),
                         reason_code=controller.processed_data.get('reason'))
        except Exception:
            pass
        observations[market] = value
    return observer.observe(dict(session_id=session.id, generation=session.generation, profile_id=session.profile_id,
                                 started_at=session.started_at, status=session.status, expires_at=session.expires_at), observations, now=time.time(),
                            monotonic_now=time.monotonic(), owner_generation=host.owner.generation)


def _liquidity_view(row, *, session_id=None, market='all'):
    if market not in ('all', 'spot', 'perpetual'):
        raise ValueError('Unknown liquidity market')
    now = float(row['database_now'])
    snapshot = deepcopy((row.get('status') or {}).get('mm_liquidity'))
    current = row.get('mm_session') or {}
    if (not snapshot or snapshot.get('session_id') != current.get('id')
            or session_id is not None and session_id != snapshot['session_id']):
        return dict(state='unavailable', session_id=session_id or current.get('id'), markets={}, measurement='sampled_owner_observations')
    observed = snapshot.get('observed_at', 0)
    age = row.get('heartbeat_age_s')
    stale = (not row.get('owner_uuid') or age is None or not 0 <= float(age) <= 5 or not 0 <= now - observed <= 5
             or snapshot.get('generation') != current.get('generation'))
    snapshot['state'] = 'unavailable' if stale else 'observed'
    selected = {}
    for key, value in snapshot['markets'].items():
        if market not in ('all', key):
            continue
        elapsed = max(value['elapsed_seconds'], (snapshot.get('ended_at') or now) - snapshot['started_at']) if snapshot.get('started_at') is not None else 0
        value.update(elapsed_seconds=elapsed, unknown_seconds=max(0, elapsed - value['observed_seconds']))
        value['two_sided_fraction_of_elapsed'] = value['two_sided_seconds'] / elapsed if elapsed else None
        if stale:
            value.update(valid=False, eligible=False, reason_code='owner_observation_unavailable', best_bid=None, best_ask=None,
                         bid_notional=None, ask_notional=None, spread_bps=None, two_sided=False)
        selected[key] = value
    snapshot['markets'] = selected
    return snapshot


def liquidity_view(row, *, session_id=None, market='all'):
    if market not in ('all', 'spot', 'perpetual'):
        raise ValueError('Unknown liquidity market')
    if session_id is not None and (not isinstance(session_id, str) or not 1 <= len(session_id) <= 128):
        raise ValueError('Invalid session identity')
    try:
        return _liquidity_view(row, session_id=session_id, market=market)
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        return dict(state='unavailable', session_id=session_id, markets={}, measurement='sampled_owner_observations')

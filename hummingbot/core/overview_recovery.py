"""Bounded reporting-only fill-to-quote recovery observations.

Durations are sampled upper bounds to the first observed acknowledged
two-sided quote after a fill. Event logs are bounded, so history is incomplete.
This module performs no connector reads, subscriptions, exchange calls or
trading-state mutations.
"""
import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone

MARKETS = ('spot', 'perpetual')
WINDOW_SECONDS = 1800
MAX_EVENTS = 1000
DEFAULT_MAX_SAMPLES = 2000
BASIS = 'fill_to_acknowledged_two_sided_quotes'


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value):
    if not _number(value):
        raise ValueError('invalid_timestamp')
    return datetime.fromtimestamp(value, timezone.utc)


def _key(market, order_id, trade_id):
    raw = json.dumps([market, order_id, trade_id], separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(percentile * len(ordered))) - 1]


def _empty_market(session_id, generation, owner_generation, now, monotonic_now):
    return dict(session_id=session_id, generation=generation, owner_generation=owner_generation,
                window_start=now, last_wall=now, last_monotonic=monotonic_now,
                last_valid=False, pending=[], recovered=[], seen=[], history_complete=False)


class PostFillRecoveryObserver:
    """Track recent session fills and their first sampled post-fill two-sided quote."""

    def __init__(self, checkpoint=None, *, max_samples=DEFAULT_MAX_SAMPLES):
        if type(max_samples) is not int or max_samples < 1:
            raise ValueError('max_samples must be a positive integer')
        self.max_samples = max_samples
        source = deepcopy(checkpoint) if isinstance(checkpoint, dict) else {}
        source_markets = source.get('markets') if isinstance(source.get('markets'), dict) else {}
        self.frame = {'markets': {}}
        for market in MARKETS:
            state = source_markets.get(market)
            if not isinstance(state, dict):
                continue
            recovered = []
            recovered_keys = set()
            raw_recovered = state.get('recovered')
            if isinstance(raw_recovered, list):
                for item in raw_recovered:
                    if (not isinstance(item, dict) or not isinstance(item.get('key'), str)
                            or not _number(item.get('timestamp')) or not _number(item.get('seconds'))
                            or item['seconds'] < 0 or item['key'] in recovered_keys):
                        continue
                    recovered_keys.add(item['key'])
                    recovered.append(dict(key=item['key'], timestamp=item['timestamp'], seconds=item['seconds']))
            pending = []
            pending_keys = set()
            raw_pending = state.get('pending')
            if isinstance(raw_pending, list):
                for item in raw_pending:
                    if (not isinstance(item, dict) or not isinstance(item.get('key'), str)
                            or not _number(item.get('timestamp')) or item['key'] in pending_keys
                            or item['key'] in recovered_keys):
                        continue
                    pending_keys.add(item['key'])
                    pending.append(dict(key=item['key'], timestamp=item['timestamp'], recoverable=False))
            combined = [(item['timestamp'], 'pending', item) for item in pending]
            combined += [(item['timestamp'], 'recovered', item) for item in recovered]
            combined.sort(key=lambda row: row[0], reverse=True)
            selected = combined[:max_samples]
            restored_pending = [item for _, kind, item in selected if kind == 'pending']
            restored_recovered = [item for _, kind, item in selected if kind == 'recovered']
            selected_keys = {item['key'] for _, _, item in selected}
            raw_seen = state.get('seen')
            seen = []
            if isinstance(raw_seen, list):
                for identity in raw_seen:
                    if isinstance(identity, str) and identity not in seen and identity not in selected_keys:
                        seen.append(identity)
            seen = (seen + [item['key'] for _, _, item in selected])[-max_samples:]
            self.frame['markets'][market] = dict(
                session_id=state.get('session_id') if isinstance(state.get('session_id'), str) else None,
                generation=state.get('generation') if type(state.get('generation')) is int else None,
                owner_generation=state.get('owner_generation') if type(state.get('owner_generation')) is int else None,
                window_start=state.get('window_start') if _number(state.get('window_start')) else 0,
                last_wall=state.get('last_wall') if _number(state.get('last_wall')) else 0,
                last_monotonic=state.get('last_monotonic') if _number(state.get('last_monotonic')) else 0,
                last_valid=False, pending=restored_pending, recovered=restored_recovered,
                seen=seen, history_complete=False)

    @property
    def checkpoint(self):
        """Return a detached JSON-safe frame for the existing status checkpoint."""
        return deepcopy(self.frame)

    def _unavailable(self, now, reason):
        try:
            observed_at = _timestamp(now)
        except (ValueError, OverflowError, OSError):
            observed_at = None
        return dict(value=None, observed_at=observed_at, stale=True, unavailable_reason=reason)

    def _view(self, state, now):
        cutoff = now - WINDOW_SECONDS
        state['recovered'] = [item for item in state['recovered']
                              if _number(item.get('timestamp')) and cutoff < item['timestamp'] <= now]
        state['pending'] = [item for item in state['pending']
                            if _number(item.get('timestamp')) and cutoff < item['timestamp'] <= now]
        durations = [item['seconds'] for item in state['recovered']
                     if _number(item.get('seconds')) and item['seconds'] >= 0]
        return dict(basis=BASIS, window_start=_timestamp(max(state['window_start'], cutoff)),
                    window_end=_timestamp(now), recovered_count=len(durations), pending_count=len(state['pending']),
                    history_complete=False, p50_seconds=_percentile(durations, .50),
                    p95_seconds=_percentile(durations, .95))

    def observe(self, session, observations, *, now, monotonic_now, owner_generation):
        """Observe markets; order_ids must already be attributed to this session."""
        session_id = session.get('id', session.get('session_id')) if isinstance(session, dict) else None
        generation = session.get('generation') if isinstance(session, dict) else None
        if (not isinstance(session_id, str) or not session_id or type(generation) is not int
                or not _number(now) or not _number(monotonic_now) or type(owner_generation) is not int):
            return {market: self._unavailable(now, 'session_or_clock_unavailable') for market in MARKETS}
        if not isinstance(observations, dict):
            observations = {}

        result = {}
        frames = self.frame['markets']
        for market in MARKETS:
            observation = observations.get(market)
            if not isinstance(observation, dict) or observation.get('valid') is not True:
                result[market] = self._unavailable(now, 'market_observation_unavailable')
                prior = frames.get(market)
                if isinstance(prior, dict):
                    prior['last_valid'] = False
                continue
            order_ids, events = observation.get('order_ids'), observation.get('events')
            if (not isinstance(order_ids, (set, frozenset, list, tuple))
                    or not isinstance(events, (list, tuple)) or len(events) > MAX_EVENTS
                    or any(not isinstance(identity, str) or not identity for identity in order_ids)):
                result[market] = self._unavailable(now, 'fill_attribution_unavailable')
                prior = frames.get(market)
                if isinstance(prior, dict):
                    prior['last_valid'] = False
                continue
            state = frames.get(market)
            if (not isinstance(state, dict) or state.get('session_id') != session_id
                    or not _number(state.get('window_start'))):
                state = _empty_market(session_id, generation, owner_generation, now, monotonic_now)
            # Expire before admission, including after an earlier capacity error.
            # Otherwise a full old window could prevent every future refresh.
            cutoff = now - WINDOW_SECONDS
            state['pending'] = [item for item in state.get('pending', [])
                                if isinstance(item, dict) and _number(item.get('timestamp')) and cutoff < item['timestamp'] <= now]
            state['recovered'] = [item for item in state.get('recovered', [])
                                  if isinstance(item, dict) and _number(item.get('timestamp')) and cutoff < item['timestamp'] <= now]
            wall_delta = now - state.get('last_wall', now)
            monotonic_delta = monotonic_now - state.get('last_monotonic', monotonic_now)
            continuous = (state.get('session_id') == session_id and state.get('generation') == generation
                          and state.get('owner_generation') == owner_generation and state.get('last_valid') is True
                          and 0 < wall_delta <= 5 and 0 < monotonic_delta <= 5
                          and abs(wall_delta - monotonic_delta) <= 1)
            if not continuous:
                for pending in state.get('pending', []):
                    if isinstance(pending, dict):
                        pending['recoverable'] = False

            pending_by_key = {item.get('key'): item for item in state.get('pending', [])
                              if isinstance(item, dict) and isinstance(item.get('key'), str)}
            recovered_keys = {item.get('key') for item in state.get('recovered', []) if isinstance(item, dict)}
            seen = state.get('seen') if isinstance(state.get('seen'), list) else []
            seen_set = {item for item in seen if isinstance(item, str)}
            previous_wall = state.get('last_wall') if _number(state.get('last_wall')) else None
            invalid_reason = None
            for event in events:
                if not isinstance(event, dict):
                    invalid_reason = 'fill_event_unavailable'
                    break
                order_id = event.get('order_id')
                if not isinstance(order_id, str) or not order_id:
                    invalid_reason = 'fill_attribution_unavailable'
                    break
                if order_id not in order_ids:
                    continue
                trade_id, timestamp = event.get('exchange_trade_id'), event.get('timestamp')
                if not isinstance(trade_id, str) or not trade_id or not _number(timestamp):
                    invalid_reason = 'fill_attribution_unavailable'
                    break
                if timestamp > now:
                    invalid_reason = 'fill_timestamp_in_future'
                    break
                identity = _key(market, order_id, trade_id)
                if identity in seen_set or identity in pending_by_key or identity in recovered_keys:
                    continue
                if timestamp <= now - WINDOW_SECONDS:
                    continue
                if len(pending_by_key) + len(state.get('recovered', [])) >= self.max_samples:
                    invalid_reason = 'sample_limit_exceeded'
                    break
                recoverable = continuous and previous_wall is not None and previous_wall <= timestamp <= now
                pending_by_key[identity] = dict(key=identity, timestamp=timestamp, recoverable=recoverable)
                seen.append(identity)
                seen_set.add(identity)
                if len(seen) > self.max_samples:
                    seen_set.discard(seen.pop(0))
                    state['history_complete'] = False
            if invalid_reason:
                state['pending'] = list(pending_by_key.values())
                state['seen'] = seen[-self.max_samples:]
                state['history_complete'] = False
                state['last_valid'] = False
                frames[market] = state
                result[market] = self._unavailable(now, invalid_reason)
                continue

            if observation.get('two_sided') is True and continuous:
                for identity, pending in list(pending_by_key.items()):
                    if pending.get('recoverable') is not True:
                        continue
                    duration = now - pending['timestamp']
                    if not _number(duration) or duration < 0:
                        continue
                    state.setdefault('recovered', []).append(dict(key=identity, timestamp=now, seconds=duration))
                    recovered_keys.add(identity)
                    del pending_by_key[identity]

            cutoff = now - WINDOW_SECONDS
            pending_items = [item for item in pending_by_key.values()
                             if _number(item.get('timestamp')) and cutoff < item['timestamp'] <= now]
            recovered_items = [item for item in state.get('recovered', [])
                               if isinstance(item, dict) and _number(item.get('timestamp'))
                               and cutoff < item['timestamp'] <= now]
            state.update(session_id=session_id, generation=generation, owner_generation=owner_generation,
                         window_start=max(state.get('window_start', now), cutoff), last_wall=now,
                         last_monotonic=monotonic_now, last_valid=True, pending=pending_items,
                         recovered=recovered_items, seen=seen[-self.max_samples:], history_complete=False)
            frames[market] = state
            try:
                result[market] = dict(value=self._view(state, now), observed_at=_timestamp(now), stale=False,
                                      unavailable_reason=None)
            except (ValueError, OverflowError, OSError):
                result[market] = self._unavailable(now, 'market_observation_unavailable')
        return result

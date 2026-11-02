"""Bounded correlation of MM attempts/connector returns and independent venue samples."""
import json
from itertools import chain


def quote_sample_summary(rows, *, max_group_gap_ms=1500):
    """Summarize point samples; separated samples never prove continuous absence."""
    rows = sorted(rows, key=lambda row: row['at_ns'])
    previous, group_start, longest = None, None, None
    empty = 0
    for row in rows:
        if not row['orders']:
            empty += 1
            if (previous is None or previous['orders']
                    or (row['at_ns'] - previous['at_ns']) / 1e6 > max_group_gap_ms):
                group_start = row['at_ns']
            longest = max(longest or 0, (row['at_ns'] - group_start) / 1e6)
        previous = row
    return dict(observed_empty_venue_samples=empty,
                min_observed_live_orders=min((len(row['orders']) for row in rows), default=None),
                max_empty_sample_span_ms=longest, empty_group_max_gap_ms=max_group_gap_ms)


def correlate_activity(directory, episodes, low, high, *, enabled):
    paths = sorted((directory / 'incidents').glob('*-incidents.jsonl'))
    venue_path = directory / 'incidents/venue-activity.jsonl'
    if venue_path.exists():
        paths.append(venue_path)
    mm_path = directory / 'incidents/mm-activity.jsonl'
    if mm_path.exists():
        paths.append(mm_path)
    states = []
    for episode in episodes:
        states.append(dict(episode=episode, start=max(low, episode['failure_ns'] - 2_000_000_000),
                           end=min(high, (episode['recovery_ns'] or episode['failure_ns'] + 15_000_000_000) + 2_000_000_000),
                           engine={}, venue={}, overflow=False, errors=0))

    def records(path):
        with path.open() as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)

    for row in chain.from_iterable(records(path) for path in paths):
        kind = row['kind']
        if kind not in ('mm_activity', 'venue_quote_sample', 'venue_quote_error'):
            continue
        for state in states:
            if not state['start'] <= row['at_ns'] <= state['end']:
                continue
            if kind == 'mm_activity':
                if row.get('capturing_role') != 'engine':
                    continue
                key = row['process_id'], row['event_id'], row['phase'], row['at_ns']
                target, capacity = state['engine'], 256
            elif kind == 'venue_quote_sample':
                key, target, capacity = row['at_ns'], state['venue'], 32
            else:
                state['errors'] += 1
                continue
            if key in target:
                continue
            if len(target) >= capacity:
                state['overflow'] = True
                continue
            target[key] = dict(row)  # Per-episode labels must not mutate another episode.

    for state in states:
        episode = state['episode']
        failure, recovery = episode['failure_ns'], episode['recovery_ns']
        end = min(high, recovery or failure + 15_000_000_000)
        engine = sorted(state['engine'].values(), key=lambda r: r['at_ns'])
        venue = sorted(state['venue'].values(), key=lambda r: r['at_ns'])
        aligned = [r for r in venue if r.get('clock_aligned') is True and
                   r['request_start_ns'] <= r['at_ns'] <= r['response_end_ns']]
        invalid = len(venue) - len(aligned)
        tracking_loss = any(not r.get('tracking_complete') for r in aligned)
        for row in engine:
            start = row.get('start_ns', row.get('previous_start_ns'))
            row['intersects_probe_transition'] = start is not None and start <= end and row['at_ns'] >= failure
        returns = [r for r in engine if r['phase'] == 'end' and r['operation'] in ('order.submit', 'order.cancel')
                   and r['intersects_probe_transition']]
        ages = [o['age_ms'] for r in aligned for o in r['orders'] if o['age_ms'] >= 0]
        terminals = {}
        for sample in aligned:
            for row in sample['terminals']:
                if state['start'] <= row['at_ns'] <= state['end']:
                    key = row['at_ns'], row['sequence'], row['order_id']
                    if len(terminals) < 256 or key in terminals:
                        terminals[key] = row
                    else:
                        state['overflow'] = True

        def max_gap(operation):
            values = [r['gap_ms'] for r in engine if r['operation'] == operation and r['phase'] == 'start'
                      and r.get('gap_ms') is not None and r['intersects_probe_transition']]
            return max(values) if values else None
        before = any(r['at_ns'] < failure for r in aligned)
        after = recovery is not None and any(r['at_ns'] > recovery for r in aligned)
        sample_gap = max(((b['at_ns'] - a['at_ns']) / 1e6 for a, b in zip(aligned, aligned[1:])), default=None)
        status = 'not_instrumented' if not enabled and not engine and not venue else 'observed'
        if status == 'observed' and (not engine or not aligned or invalid or tracking_loss or state['errors']
                                     or state['overflow'] or not before or not after or (sample_gap or 0) > 1500):
            status = 'partial'
        episode['trading_activity'] = dict(
            status=status, context_start_ns=state['start'], context_end_ns=state['end'],
            engine_events=engine, venue_samples=venue, evidence_overflow=state['overflow'],
            venue_observer_errors=state['errors'], invalid_venue_clock_samples=invalid,
            venue_tracking_incomplete=tracking_loss,
            has_venue_sample_before_failure=before, has_venue_sample_after_recovery=after,
            max_venue_sample_gap_ms=sample_gap, **quote_sample_summary(aligned),
            max_strategy_tick_start_gap_ms=max_gap('strategy.tick'),
            max_controller_cycle_start_gap_ms=max_gap('controller.cycle'),
            max_observed_live_order_age_ms=max(ages) if ages else None,
            order_call_returns=returns, venue_terminals=sorted(terminals.values(), key=lambda r: r['at_ns']),
            continuous_quoting_proven=False,
            interpretation='Attempt gaps and resting-order ages are not quote downtime. A completed tick does not prove '
            'admission; a connector return is not terminal venue readback. Samples are discrete and incident capture '
            'is bounded; inspect runtime/file loss and observer gaps. Clocks align only on the shared Linux kernel, '
            'not a remote OKX server. API incidents also use engine activity as an independent observation.')

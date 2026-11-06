"""New-intent checkpoint reads preserve durability and ordinary dirty checking."""
import json
import re
import time
from decimal import Decimal
from statistics import median
from test.integration.postgres.test_ownership import intent, owned as _owned
from test.integration.postgres.test_performance import queries, report
from unittest.mock import patch

import pytest
from sqlalchemy import event, insert, inspect, select, update

from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order

owned = _owned


def checkpoint(context):
    with context.manager.get_new_session() as session, session.begin():
        context.recorder.save_market_states('test.yml', context.market, session)


def stored(context):
    with context.engine.connect() as connection:
        return connection.execute(select(MarketState.saved_state).where(
            MarketState.config_file_path == 'test.yml',
            MarketState.market == context.market.display_name)).scalar_one()


def seed(context, *, large=False, pending=False):
    tracked = intent(context, 'existing')
    state = tracked.to_json()
    if large:
        state = {**state, 'synthetic_checkpoint_payload': 'x' * (1024 * 1024)}
    old = {'existing': state}
    with context.engine.begin() as connection:
        connection.execute(update(Order).where(Order.id == 'existing').values(creation_pending=pending))
        connection.execute(update(MarketState).where(MarketState.market == context.market.display_name).values(saved_state=old))
    return old


def tracking_reads(statements):
    return [sql for sql in statements if sql.lstrip().upper().startswith('SELECT')
            and re.search(r'(?:SELECT\s+|,\s*)market_states\.saved_state(?:\s+AS\s+market_states_saved_state)?\s*(?:,|$)',
                          sql.split('FROM')[0])]


@pytest.mark.parametrize('case', ['new_row', 'acknowledged', 'covered_pending', 'discard_acknowledged'])
def test_new_intent_does_not_read_replaced_old_tracking_json(owned, case):
    if case != 'new_row':
        seed(owned, pending=case == 'covered_pending')
    if case == 'discard_acknowledged':
        owned.market.tracking_states = {}
    with queries(owned.engine) as statements:
        intent(owned, 'new-intent')
    assert stored(owned) == owned.market.tracking_states
    assert not tracking_reads(statements), 'A fresh intent must not materialize unused old tracking JSON'


@pytest.mark.parametrize('large', [False, True])
def test_ordinary_unchanged_checkpoint_keeps_full_read_without_extra_json_update(owned, large):
    before = seed(owned, large=large)
    owned.market.tracking_states = dict(before)
    with queries(owned.engine) as statements:
        checkpoint(owned)
    assert stored(owned) == before
    assert len(tracking_reads(statements)) == 1
    assert not any(sql.startswith('UPDATE market_states') and 'saved_state=' in sql for sql in statements)


@pytest.mark.parametrize('count', [1, 2])
def test_new_intent_loads_missing_pending_tracking_once_and_cold_restore_is_full(owned, count):
    missing = {f'lost-{i}': intent(owned, f'lost-{i}').to_json() for i in range(count)}
    owned.market.tracking_states = {}
    with queries(owned.engine) as statements:
        intent(owned, 'new-intent')
    expected = {**missing, **owned.market.tracking_states}
    assert stored(owned) == expected
    assert len(tracking_reads(statements)) == 1
    assert not set(missing).intersection(owned.market.tracking_states)
    with owned.manager.get_new_session() as session:
        row = owned.recorder.get_market_states('test.yml', owned.market, session)
        assert 'saved_state' not in inspect(row).unloaded
        assert row.saved_state == expected
    owned.recorder.restore_market_states('test.yml', owned.market)
    owned.market.restore_tracking_states.assert_called_with(expected)
    with owned.engine.begin() as connection:
        connection.execute(update(Order).where(Order.id.in_(missing)).values(creation_pending=False))
    checkpoint(owned)
    assert stored(owned) == owned.market.tracking_states


def test_new_intent_without_current_tracking_retains_full_checkpoint_read(owned):
    before = seed(owned, pending=True)
    order = InFlightOrder('not-tracked', 'BTC-USDT', OrderType.LIMIT, TradeType.BUY,
                          Decimal('0.1'), 1700000000, Decimal('100'))
    with queries(owned.engine) as statements:
        owned.recorder.record_order_intent(owned.market, order)
    assert stored(owned) == before
    assert len(tracking_reads(statements)) == 1


@pytest.mark.parametrize('fault', ['lazy_read', 'commit'])
def test_projection_fault_keeps_missing_tracking_and_rolls_back_new_intent(owned, fault):
    intent(owned, 'lost-ack')
    before = stored(owned)
    owned.market.tracking_states = {}

    def fail_read(connection, cursor, statement, parameters, context, many):
        if tracking_reads([statement]):
            raise OSError('Synthetic checkpoint read failure')

    def fail_commit(session):
        raise OSError('Synthetic checkpoint commit failure')

    target, hook, callback = ((owned.engine, 'before_cursor_execute', fail_read) if fault == 'lazy_read'
                              else (owned.manager._session_cls, 'before_commit', fail_commit))
    event.listen(target, hook, callback)
    try:
        with pytest.raises(OSError, match='Synthetic'):
            intent(owned, 'new-intent')
    finally:
        event.remove(target, hook, callback)
    assert stored(owned) == before
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.id).where(Order.id == 'new-intent')).first() is None
    assert 'new-intent' not in owned.owner.sent_intents
    assert not owned.manager.persistence_healthy


def test_owner_loss_after_projected_read_cannot_commit_new_intent(owned):
    before = seed(owned)
    original = owned.recorder.get_market_states

    def read(*args, **kwargs):
        row = original(*args, **kwargs)
        owned.owner.release()
        return row

    with patch.object(owned.recorder, 'get_market_states', side_effect=read):
        with pytest.raises(OwnershipLost):
            intent(owned, 'new-intent')
    assert stored(owned) == before
    with owned.engine.connect() as connection:
        assert connection.execute(select(Order.id).where(Order.id == 'new-intent')).first() is None
    assert 'new-intent' not in owned.owner.sent_intents


@pytest.mark.parametrize('pending', [False, True])
def test_fresh_checkpoint_filter_keeps_config_and_market_scope(owned, pending):
    seed(owned)
    with owned.engine.begin() as connection:
        template = dict(connection.execute(select(Order).where(Order.id == 'existing')).mappings().one())
        for identity, config, market in [('other-config', 'other.yml', 'okx'), ('other-market', 'test.yml', 'okx_perpetual')]:
            connection.execute(insert(Order).values({**template, 'id': identity, 'config_file_path': config,
                                                     'market': market, 'creation_pending': pending}))
            connection.execute(insert(MarketState).values(config_file_path=config, market=market,
                                                          timestamp=1, saved_state={identity: {'unchanged': True}}))
    owned.market.tracking_states.update({key: {'unrelated': True} for key in ['other-config', 'other-market', 'never-admitted']})
    order = InFlightOrder('scoped-new', 'BTC-USDT', OrderType.LIMIT, TradeType.BUY,
                          Decimal('.1'), 1700000000, Decimal('100'))
    owned.market.tracking_states[order.client_order_id] = order.to_json()
    # Exercise the recorder scope directly, not permission to send past another
    # unresolved account intent. before_order retains that independent gate.
    owned.recorder.record_order_intent(owned.market, order)
    assert set(stored(owned)) == {'existing', 'scoped-new'}
    with owned.engine.connect() as connection:
        other = connection.execute(select(MarketState.config_file_path, MarketState.market, MarketState.saved_state)).all()
    assert ('other.yml', 'okx', {'other-config': {'unchanged': True}}) in other
    assert ('test.yml', 'okx_perpetual', {'other-market': {'unchanged': True}}) in other


@pytest.mark.parametrize('field,value', [('amount', '0.123456789012345678'), ('last_state', '3'),
                                         ('exchange_order_id', 'acknowledged-id')])
def test_fresh_intent_does_not_hide_changed_existing_tracking(owned, field, value):
    seed(owned)
    owned.market.tracking_states['existing'] = {**owned.market.tracking_states['existing'], field: value}
    intent(owned, 'new-intent')
    assert stored(owned) == owned.market.tracking_states


def test_duplicate_intent_identity_cannot_overwrite_checkpoint(owned):
    seed(owned)
    before = stored(owned)
    owned.market.tracking_states['existing'] = {'must_not_replace': True}
    with pytest.raises(RuntimeError, match='already exists'):
        owned.recorder.record_order_intent(owned.market, owned.market.in_flight_orders['existing'])
    assert stored(owned) == before


@pytest.mark.parametrize('case', ['small_new_intent', 'large_new_intent', 'large_unchanged_checkpoint', 'missing_pending'])
def test_intent_checkpoint_paired_measurement(owned, case):
    old = seed(owned, large=case != 'small_new_intent', pending=case == 'missing_pending')
    original = owned.recorder.get_market_states
    measurements = {mode: dict(durations_ms=[], thread_cpu_ms=[], sql_counts=[], old_json_reads=[], tracking_updates=[])
                    for mode in ['full', 'projected']}
    for repetition in range(7):
        for mode in (['full', 'projected'] if repetition % 2 == 0 else ['projected', 'full']):
            with owned.engine.begin() as connection:
                connection.execute(update(Order).where(Order.id != 'existing').values(creation_pending=False))
                connection.execute(update(MarketState).where(MarketState.market == owned.market.display_name).values(saved_state=old))
            owned.market.tracking_states = {} if case == 'missing_pending' else dict(old)
            order = InFlightOrder(f'new-{repetition}-{mode}', 'BTC-USDT', OrderType.LIMIT,
                                  TradeType.BUY, Decimal('0.1'), 1700000000, Decimal('100'))
            if case != 'large_unchanged_checkpoint':
                owned.market.tracking_states[order.client_order_id] = order.to_json()

            def read(*args, **kwargs):
                if mode == 'full':
                    kwargs['include_saved_state'] = True
                return original(*args, **kwargs)

            with patch.object(owned.recorder, 'get_market_states', side_effect=read):
                start = time.perf_counter()
                cpu = time.thread_time()
                with queries(owned.engine) as statements:
                    if case == 'large_unchanged_checkpoint':
                        checkpoint(owned)
                    else:
                        owned.recorder.record_order_intent(owned.market, order)
                elapsed = (time.perf_counter() - start) * 1000
                cpu_ms = (time.thread_time() - cpu) * 1000
            expected = {**old, **owned.market.tracking_states} if case == 'missing_pending' else owned.market.tracking_states
            assert stored(owned) == expected
            values = measurements[mode]
            values['durations_ms'].append(elapsed)
            values['thread_cpu_ms'].append(cpu_ms)
            values['sql_counts'].append(len(statements))
            values['old_json_reads'].append(len(tracking_reads(statements)))
            values['tracking_updates'].append(sum(sql.startswith('UPDATE market_states') and 'saved_state=' in sql for sql in statements))
    for values in measurements.values():
        values['p50_ms'] = median(values['durations_ms'])
        values['thread_cpu_p50_ms'] = median(values['thread_cpu_ms'])
        values['max_ms'] = max(values['durations_ms'])
    report('intent-checkpoint-' + case, dict(samples_per_mode=7, measurements=measurements,
                                             old_json_compact_bytes=len(json.dumps(old, separators=(',', ':')).encode())))

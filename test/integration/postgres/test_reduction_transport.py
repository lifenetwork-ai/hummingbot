"""Both native send gates fence close effects before any transport marker."""
import json
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from decimal import Decimal
from test.hummingbot.cli.test_reduction_admission import closing_host
from test.integration.postgres.test_ownership import assistant, owned as _owned
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text, update

from hummingbot.core.order_submission_guard import SubmissionBlocked, session_order_scope
from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.model.runtime_journal import SubmissionAttempt

owned = _owned


def prepare(context):
    host, controller, connector, state, config, order = closing_host()
    connector.display_name = connector.name
    connector.authenticator = SimpleNamespace(api_key='offline-test-only')
    connector.in_flight_orders = {'close': order}
    connector.tracking_states = {'close': order.to_json()}
    connector._format_size_to_amount.side_effect = lambda pair, size: Decimal(size)
    context.recorder._markets = [connector]
    context.owner.connectors = {'okx_perpetual': connector}
    context.owner.mm_ready_check = host.mm_runtime_ready
    context.owner.mm_order_ready_check = host.mm_runtime_order_ready
    context.owner.mm_quote_check = host.mm_quote_valid
    snapshot = dict(id='session', generation=0, status='running', started_at=time.time() - 1,
                    expires_at=time.time() + 120, step_expires_at=time.time() + 120,
                    catalogue={'markets': {'perpetual': {'inventory_reduction': {'mode': 'close_first', 'price_mode': 'breakeven'}}}})
    with context.engine.begin() as connection:
        connection.execute(text("UPDATE bot_state SET config_type='mm-session', mm_session=CAST(:state AS jsonb)"),
                           {'state': json.dumps(snapshot)})
    return host, controller, connector, state, config, order


def body(order):
    return dict(clOrdId=order.client_order_id, instId='BTC-USDT-SWAP', side='sell', posSide='long',
                ordType='post_only', tdMode='cross', px=str(order.price), sz=str(order.amount))


async def send(rest, data):
    return await rest.execute_request(url='https://offline.invalid/api/v5/trade/order', method=RESTMethod.POST,
                                      throttler_limit_id='order', is_auth_required=True, data=data,
                                      headers={'OK-ACCESS-KEY': 'offline-test-only'})


def admit(context, connector, config, order):
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        context.owner.before_order(connector, order)


def test_verified_close_can_pass_both_gates_above_opening_cap_once(owned):
    host, controller, connector, _, config, order = prepare(owned)
    assert not controller.exposure_within_caps()
    admit(owned, connector, config, order)
    rest, transport = assistant()
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        owned.loop.run_until_complete(send(rest, body(order)))
    assert transport.call.await_count == 1
    with owned.manager.get_new_session() as session:
        attempt = session.get(SubmissionAttempt, 'close')
        assert attempt.state == 'possibly_sent'
        assert attempt.context['order']['position'] == 'CLOSE'
        assert attempt.context['executor']['position_action'] == 'CLOSE'
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        with pytest.raises(SubmissionBlocked):
            owned.loop.run_until_complete(send(rest, body(order)))
    assert transport.call.await_count == 1


def test_settled_recovery_can_check_session_readiness_without_authorizing_an_order(owned):
    prepare(owned)
    recovery = owned.owner.automatic_recovery
    recovery.status = dict(phase='reconciling', operator_required=False)
    recovery.resume_scope = ('session', 0)
    owned.owner.allow_new_orders = False
    core = SimpleNamespace(trade_fill_db=owned.manager, markets_recorder=owned.recorder)
    owned.loop.run_until_complete(recovery.step(core, force=True))
    assert recovery.status['phase'] == 'idle'
    assert owned.owner.allow_new_orders


@pytest.mark.parametrize('change', ['venue', 'fee', 'session', 'halt', 'stopping_executor', 'posSide', 'extra_algo', 'stored_effect'])
def test_changed_authority_or_economic_effect_after_throttle_never_reaches_transport(owned, change):
    _, controller, connector, state, config, order = prepare(owned)
    admit(owned, connector, config, order)
    data = body(order)

    class ChangedWhileQueued:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            if change == 'venue':
                connector.account_positions['long'].amount = Decimal('.01')
            elif change == 'fee':
                controller._reduction_clock = lambda: 81
            elif change == 'session':
                with owned.engine.begin() as connection:
                    connection.execute(text("UPDATE bot_state SET mm_session=jsonb_set(mm_session, '{generation}', '1')"))
            elif change == 'halt':
                owned.owner.require_recovery('test stop before closing transport')
            elif change == 'stopping_executor':
                controller._stops_requested.add(config.id)
            elif change in ('posSide', 'extra_algo'):
                # The queued REST payload changes after local intent creation.
                data['posSide'] = 'short' if change == 'posSide' else 'long'
                if change == 'extra_algo':
                    data['attachAlgoOrds'] = [{'tpTriggerPx': '110', 'tpOrdPx': '-1'}]
            elif change == 'stored_effect':
                with owned.engine.begin() as connection:
                    value = deepcopy(connection.execute(select(SubmissionAttempt.context).where(
                        SubmissionAttempt.order_id == 'close')).scalar_one())
                    value['order']['position'] = 'OPEN'
                    connection.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == 'close').values(context=value))
            yield

    # RESTAssistant serializes the input before throttling; mutate serialized
    # request data through the real transport preprocessing hook for body cases.
    class BodyChange:
        async def pre_process(self, request):
            if change in ('posSide', 'extra_algo'):
                parsed = json.loads(request.data)
                if change == 'posSide':
                    parsed['posSide'] = 'short'
                else:
                    parsed['attachAlgoOrds'] = [{'tpTriggerPx': '110', 'tpOrdPx': '-1'}]
                request.data = json.dumps(parsed)
            return request

    rest, transport = assistant(ChangedWhileQueued())
    rest._rest_pre_processors = [BodyChange()]
    with session_order_scope('session', 0, level_id=config.level_id, executor=config.model_dump(mode='json')):
        with pytest.raises(SubmissionBlocked):
            owned.loop.run_until_complete(send(rest, data))
    transport.call.assert_not_awaited()
    with owned.manager.get_new_session() as session:
        assert session.get(SubmissionAttempt, 'close').state == 'prepared'


@pytest.mark.parametrize('missing', ['mm_order_ready_check', 'mm_quote_check'])
def test_close_first_session_cannot_trade_with_only_the_legacy_global_hook(owned, missing):
    _, _, connector, _, config, order = prepare(owned)
    setattr(owned.owner, missing, None)
    with pytest.raises(SubmissionBlocked, match='authority is unavailable'):
        admit(owned, connector, config, order)
    assert 'close' not in owned.owner.active_intents

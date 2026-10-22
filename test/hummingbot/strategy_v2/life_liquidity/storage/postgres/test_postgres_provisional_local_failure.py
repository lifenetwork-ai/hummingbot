"""A local provisional refusal is neither an exchange event nor a send fact."""
import json
from copy import deepcopy
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_unused_release import cold_observer

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.event.event_forwarder import EventForwarder
from hummingbot.core.event.events import MarketEvent
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_local_provisional_prepare_refusal_keeps_original_cause_and_closes_without_exchange_failure(database, monkeypatch, side):
    t, service = await configured(database, provisional_quotes=True, maker_cycle=True)
    failures, reached = [], []
    listener = EventForwarder(failures.append)
    t.connector.add_listener(MarketEvent.OrderFailure, listener)
    cause = StorageError('PG_SYNTHETIC_ORIGINAL_PREPARE_REFUSAL')

    async def refused(*args, **kwargs):
        reached.append(True)
        raise cause
    monkeypatch.setattr(service.journal.births, 'prepare', refused)
    primary = None
    try:
        with pytest.raises(StorageError) as caught:
            await service.quote('local-provisional-refusal', side, 0)
        assert reached == [True], 'Did not reach the original preparation boundary'
        assert str(caught.value) == 'PG_SPOT_SUBMISSION_UNKNOWN' and caught.value.__cause__ is cause
        assert not failures, 'A never-started SDK request emitted an exchange OrderFailure'
        assert not t.service_orders and not t.connector._order_tracker.active_orders
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            await service.close()
        except BaseException as error:
            if primary is None:
                raise
            primary.add_note('cleanup=' + type(error).__name__ + ':' + str(error))
        t.connector.remove_listener(MarketEvent.OrderFailure, listener)
    # Retain diagnostics under the original birth without creating any
    # preparation/send/permission. Cold observation is allowed, never retry.
    assert await database[0].fetchval('SELECT count(*) FROM life_recorder."Executors"') == 1
    assert await database[0].fetchval('SELECT bool_and(status=4) FROM life_recorder."Executors"')
    assert not service.runtime._tasks and all(p._closed for p in service.runtime._pools.values())
    payload = json.loads(await database[0].fetchval("SELECT body#>>'{command,payload_json}' FROM life_financial.recorder_outbox WHERE stream LIKE 'executor:%' ORDER BY sequence DESC LIMIT 1"))
    query = 'SELECT life_financial.recorder_executor_origin($1,$2::jsonb,$3::jsonb)'
    args = t.cfg.account_id, json.dumps(t.cfg.binding.json())
    await database[0].execute(query, *args, json.dumps(payload))
    for damage in ('wire', 'config', 'binding', 'amount', 'fee'):
        invalid = deepcopy(payload)
        if damage == 'wire':
            invalid['custom_info']['recovery_order_ids'] = ['foreign-wire']
        elif damage == 'config':
            invalid['config']['amount'] = '123'
        elif damage == 'binding':
            invalid['custom_info']['life_storage_binding']['account_uid'] = 'foreign'
        else:
            invalid['filled_amount_quote' if damage == 'amount' else 'cum_fees_quote'] = '0.1'
        with pytest.raises(asyncpg.RaiseError, match='LIFE_RECORDER_ORIGIN_INVALID'):
            await database[0].execute(query, *args, json.dumps(invalid))
    cold = await cold_observer(t, service)
    try:
        assert len(cold.reporting.startup_infos) == 1 and cold.quote_application is None
        before = await cold.financial.snapshot(t.cfg.binding)
        ticket = await database[0].fetchval('SELECT ticket_id FROM life_financial.quote_births')
        await cold.retire_unprepared_birth('retire-local-refusal', ticket)
        assert (await cold.release_unused_claim('release-local-refusal', ticket)).released
        after = await cold.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not t.service_orders and cold._revoked
    finally:
        await cold.close()

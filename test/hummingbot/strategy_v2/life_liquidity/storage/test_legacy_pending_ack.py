import copy
import json
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_recovery import (
    ack,
    bundle as bundle_fixture,
    cold_open,
    facts,
    pending_ack,
)
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_funding_history import _completed_acks

bundle = bundle_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wal', 'receipt'])
async def test_pending_ack_view_matches_real_original_resume_without_money_or_budget_changes(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    await pending_ack(store, binding, owner, stage)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        resources = thaw((await cold.read(binding)).resources)
        raw = await cold._call(cold._raw)
        records = json.loads(raw['wal'])['records']
        command = ack(owner)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres import legacy_pending_ack as module
        result, wal, pending = module.ack_view(resources, records, (command,), observed_at=facts(binding).account.observed_at)
        assert pending == [command.operation_id]
        future = replace(command, observed_at=command.observed_at + timedelta(seconds=1))
        future_resources = copy.deepcopy(resources)
        future_row = future_resources['acknowledgements'][command.operation_id]
        future_row['command'] = future.json()
        future_row['fingerprint'] = fingerprint({'command': future.json(), 'owner': future_row['owner']})
        with pytest.raises(StorageError):
            module.ack_view(future_resources, records, (future,), observed_at=facts(binding).account.observed_at)
        _completed_acks(SimpleNamespace(spot_acks=(command,), observed_at=facts(binding).account.observed_at), result, owner.json())
        for fault in ('missing', 'duplicate', 'hash', 'wire', 'state', 'exchange', 'owner'):
            changed, altered = copy.deepcopy(resources), copy.deepcopy(records)
            chosen = (command,)
            if fault == 'missing':
                chosen = ()
            elif fault == 'duplicate':
                chosen *= 2
            elif fault == 'hash':
                changed['acknowledgements'][command.operation_id]['fingerprint'] = 'f' * 64
            elif fault == 'wire':
                altered[command.intent_id]['client_order_id'] = 'foreignwire'
            elif fault == 'state':
                altered[command.intent_id]['state'] = 'TERMINAL'
            elif fault == 'exchange':
                altered[command.intent_id]['exchange_order_id'] = 'foreign'
            else:
                changed['acknowledgements'][command.operation_id]['owner']['epoch'] += 1
            with pytest.raises(StorageError):
                module.ack_view(changed, altered, chosen, observed_at=facts(binding).account.observed_at)
        assert await cold._call(cold._raw) == raw
        before = await cold.snapshot(binding)
        await cold.resume_ack(command, facts=facts(binding), cut=before.cut, owner=owner)
        complete = thaw((await cold.read(binding)).resources)
        assert result['commands'] == complete['commands']
        assert all({key: complete['acknowledgements'][op][key] for key in row} == row for op, row in result['acknowledgements'].items())
        assert wal == json.loads((await cold._call(cold._raw))['wal'])['records']
        after = await cold.snapshot(binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
    finally:
        await cold.close()

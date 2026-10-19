import copy
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command as fill_command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    bundle as bundle_fixture,
    cold_open,
    facts,
    pending_fill,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_recovery_batch import batch, full_facts

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch

bundle = bundle_fixture


@pytest.mark.asyncio
@pytest.mark.parametrize('superseded', [False, True, 2])
async def test_complete_original_recovery_history_retains_exact_children_without_new_money(bundle, tmp_path, monkeypatch, superseded):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        predecessors = []
        for name in ('first-batch', 'intermediate-batch')[:int(superseded)]:
            first = SpotRecoveryBatch(name, (replace(fill_command(owner), operation_id='first-fill'),), (), (), tuple(predecessors))
            publish = cold._publish_recovery_batch

            def fail(key, *args):
                if key.startswith('batch-done:'):
                    raise OSError('synthetic original batch receipt loss')
                return publish(key, *args)
            with monkeypatch.context() as patch:
                patch.setattr(cold, '_publish_recovery_batch', fail)
                with pytest.raises(CommitUnknown):
                    await cold.recover_spot_batch(first, facts=facts(binding), acknowledgements=(), cut=(await cold.snapshot(binding)).cut, owner=owner)
            await cold.close()
            cold = await cold_open(files, binding, tmp_path)
            predecessors.append(name)
        current = replace(batch(owner), operation_id='second-batch', supersedes=tuple(predecessors))
        await cold.recover_spot_batch(current, facts=full_facts(binding), acknowledgements=(), cut=(await cold.snapshot(binding)).cut, owner=owner)
        resources = thaw((await cold.read(binding)).resources)
        before, raw = await cold.snapshot(binding), await cold._call(cold._raw)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_recovery_history import (
            completed_recovery_history,
        )
        proof = completed_recovery_history(resources, full_facts(binding))
        assert len(proof['spot_recovery_history']) == 1 + len(predecessors)
        assert await cold._call(cold._raw) == raw and await cold.snapshot(binding) == before
        if len(predecessors) == 2:
            cycle = copy.deepcopy(resources)
            row = cycle['recovery_batches']['first-batch']
            row['command']['supersedes'] = ['intermediate-batch']
            row['fingerprint'] = fingerprint({'command': row['command'], 'owner': row['owner']})
            with pytest.raises(StorageError):
                completed_recovery_history(cycle, full_facts(binding))
        if superseded:
            # Both child receipts are real and complete. Reciprocal links and
            # individually valid children still cannot justify a successor
            # which omits an original predecessor trade.
            omitted = copy.deepcopy(resources)
            predecessor, successor = (omitted['recovery_batches'][key] for key in ('first-batch', 'second-batch'))
            predecessor['command']['fills'], successor['command']['fills'] = successor['command']['fills'], predecessor['command']['fills']
            predecessor['facts'] = copy.deepcopy(successor['facts'])
            for row in (predecessor, successor):
                row['fingerprint'] = fingerprint({'command': row['command'], 'owner': row['owner']})
            with pytest.raises(StorageError):
                completed_recovery_history(omitted, full_facts(binding))
        for fault in ('state', 'hash', 'owner', 'source', 'liability', 'scope', 'trades', 'child', 'child_hash', 'successor'):
            data = copy.deepcopy(resources)
            row = data['recovery_batches']['second-batch']
            if fault == 'state':
                row['state'] = 'APPLYING'
            elif fault == 'hash':
                row['fingerprint'] = 'f' * 64
            elif fault == 'owner':
                row['owner']['epoch'] += 1
            elif fault == 'source':
                row['facts']['binding']['account_uid'] = 'foreign'
            elif fault == 'liability':
                row['facts']['liability_quote_usdt'] = '1'
            elif fault == 'scope':
                row['facts']['runner_scope_complete'] = False
            elif fault == 'trades':
                row['facts']['account_trade_ids'] = []
            elif fault == 'child':
                del data['fill_operations'][current.fills[0].operation_id]
            elif fault == 'child_hash':
                data['fill_operations'][current.fills[0].operation_id]['fingerprint'] = 'f' * 64
            else:
                row['completed_by'] = 'invented'
            with pytest.raises(StorageError):
                completed_recovery_history(data, full_facts(binding))
    finally:
        await cold.close()

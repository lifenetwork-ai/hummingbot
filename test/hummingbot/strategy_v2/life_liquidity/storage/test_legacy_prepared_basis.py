import copy
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_prepared_receipts import pending_receipt

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, thaw
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_prepared_basis import prepared_view


@pytest.mark.asyncio
async def test_original_pending_prepared_receipt_is_a_complete_hold_and_never_a_send_or_release(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, owner, prepared, facts = await pending_receipt(tmp_path, monkeypatch)
    try:
        resources = thaw((await store.read(store.binding)).resources)
        raw = await store._call(store._raw)
        wallet, subsidy, records = json.loads(raw['reservations']), json.loads(raw['subsidy']), json.loads(raw['wal'])['records']
        args = resources, wallet, subsidy, records
        result, pending = prepared_view(*args, (prepared,), owner.json(), store.binding.json(), facts.observed_at)
        assert pending == [prepared.operation_id] and result['commands'][prepared.operation_id]['state'] == 'PREPARED'
        assert thaw((await store.read(store.binding)).resources) == resources
        assert await store._call(store._raw) == raw
        for fault in ('missing', 'duplicate', 'owner', 'hash', 'binding', 'wal', 'principal', 'subsidy', 'time', 'arm'):
            data = copy.deepcopy(args)
            selected = (prepared,)
            row = data[0]['commands'][prepared.operation_id]
            if fault == 'missing':
                selected = ()
            elif fault == 'duplicate':
                selected *= 2
            elif fault == 'owner':
                row['original_owner']['epoch'] += 1
            elif fault == 'hash':
                row['fingerprint'] = 'f' * 64
            elif fault == 'binding':
                row['original_cut']['checkpoint']['binding']['account_uid'] = 'foreign'
            elif fault == 'wal':
                data[3][prepared.intent_id]['state'] = 'SEND_UNKNOWN'
            elif fault == 'principal':
                data[1]['reservations'][prepared.intent_id]['remaining_base'] = '0'
            elif fault == 'subsidy':
                data[2]['entries'][prepared.intent_id]['actual'] = '0'
            elif fault == 'time':
                row['prepared_at'] = '2000-01-01T00:00:00+00:00'
            else:
                row['arm_fingerprint'] = 'f' * 64
            with pytest.raises(StorageError):
                prepared_view(*data, selected, owner.json(), store.binding.json(), facts.observed_at)
    finally:
        await store.close()

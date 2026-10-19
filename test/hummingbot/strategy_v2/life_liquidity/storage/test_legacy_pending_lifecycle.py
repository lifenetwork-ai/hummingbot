import copy
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, thaw
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_cancel_basis import cancel_basis
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_lifecycle_basis import abort_basis
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_lifecycle import lifecycle_view
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand

pytest_plugins = ['test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts']


@pytest.mark.asyncio
@pytest.mark.parametrize('action,stage', [('ABORT_PREPARED', 'wal'), ('ABORT_PREPARED', 'principal'), ('ABORT_PREPARED', 'subsidy'), ('ABORT_PREPARED', 'receipt'),
                                          ('REQUEST_CANCEL', 'wal'), ('REQUEST_CANCEL', 'principal'), ('REQUEST_CANCEL', 'receipt'),
                                          ('RECORD_CANCEL_ATTEMPT', 'wal'), ('RECORD_CANCEL_ATTEMPT', 'principal'), ('RECORD_CANCEL_ATTEMPT', 'receipt')])
@pytest.mark.parametrize('written', [False, True])
async def test_interrupted_lifecycle_view_preserves_origin_and_matches_real_file_completion(bundle, monkeypatch, action, stage, written):
    store, files, binding, owner, clock = bundle
    prepared = order(owner)
    await store.prepare(prepared, cut=(await store.snapshot(binding)).cut, owner=owner)
    if action != 'ABORT_PREPARED':
        await store.arm(prepared.operation_id, cut=(await store.snapshot(binding)).cut, owner=owner)
    command = SpotLifecycleCommand('pending-lifecycle', prepared.operation_id, prepared.intent_id, prepared.client_order_id, action,
                                   clock[0] if action == 'RECORD_CANCEL_ATTEMPT' else None,
                                   0 if action == 'RECORD_CANCEL_ATTEMPT' else None,
                                   100 if action == 'RECORD_CANCEL_ATTEMPT' else None)
    target, method = {'wal': (store._wal, 'abort_before_send' if action == 'ABORT_PREPARED' else 'mark_cancel_attempt' if action == 'RECORD_CANCEL_ATTEMPT' else 'mark_cancel_requested'),
                      'principal': (store._ledger, 'abort_unsent' if action == 'ABORT_PREPARED' else 'request_cancel'),
                      'subsidy': (store._subsidy, 'release_unsent'), 'receipt': (store, '_publish_lifecycle')}[stage]
    original = getattr(target, method)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('lifecycle-done:'):
            return original(*args, **kwargs)
        if written:
            original(*args, **kwargs)
        raise OSError('synthetic original lifecycle interruption')
    with monkeypatch.context() as patch:
        patch.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_lifecycle(command, cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.close()
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import BUDGET, CAPACITY, RISK

    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=files.binding.parent / 'account-locks', clock=lambda: clock[0])
    try:
        resources = thaw((await store.read(binding)).resources)
        raw = await store._call(store._raw)
        args = resources, json.loads(raw['reservations']), json.loads(raw['subsidy']), json.loads(raw['wal'])['records']
        result, wallet, subsidy, wal, pending = lifecycle_view(*args)
        proof = abort_basis(result, wal, wallet, subsidy, SimpleNamespace(accepted_wire_ids=(), runner_wire_ids=())) if action == 'ABORT_PREPARED' else cancel_basis(result, wal)
        assert proof
        assert pending == ([] if stage == 'receipt' and written else [command.operation_id])
        for fault in ('owner', 'identity', 'result', 'wire', 'wal_state', 'counter_or_exchange'):
            data = copy.deepcopy(args)
            row = data[0]['lifecycle_operations'][command.operation_id]
            if fault == 'owner':
                row['owner']['epoch'] += 1
            elif fault == 'identity':
                row['identity'] = 'f' * 64
            elif fault == 'result':
                row['result'] = 'TERMINAL'
            elif fault == 'wire':
                data[0]['commands'][prepared.operation_id]['command']['client_order_id'] = 'foreignwire'
            elif fault == 'wal_state':
                data[3][prepared.intent_id]['state'] = 'TERMINAL'
            elif action == 'ABORT_PREPARED':
                data[3][prepared.intent_id]['exchange_order_id'] = 'invented'
            else:
                data[3][prepared.intent_id]['cancel_attempts'] += 3
            with pytest.raises(StorageError):
                changed, held, costs, records, _ = lifecycle_view(*data)
                if action == 'ABORT_PREPARED':
                    abort_basis(changed, records, held, costs, SimpleNamespace(accepted_wire_ids=(), runner_wire_ids=()))
                else:
                    cancel_basis(changed, records)
        assert thaw((await store.read(binding)).resources) == resources and await store._call(store._raw) == raw
        # Re-run the actual File command to establish the independent final image.
        await store.apply_lifecycle(command, cut=(await store.snapshot(binding)).cut, owner=owner)
        complete = thaw((await store.read(binding)).resources)
        after = await store._call(store._raw)
        assert result['lifecycle_operations'] == complete['lifecycle_operations']
        assert result['commands'] == complete['commands']
        assert wal == json.loads(after['wal'])['records']
        for key in wallet['reservations']:
            assert wallet['reservations'][key] == json.loads(after['reservations'])['reservations'][key]
        for key in subsidy['entries']:
            a, b = subsidy['entries'][key], json.loads(after['subsidy'])['entries'][key]
            assert D(a['actual']) == D(b['actual']) if a['actual'] is not None else b['actual'] is None
        assert not (await store.check_spot_send(prepared, owner=owner)).qualified
    finally:
        await store.close()

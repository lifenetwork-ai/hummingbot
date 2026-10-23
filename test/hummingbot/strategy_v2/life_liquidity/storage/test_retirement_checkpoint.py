"""A target verifies both signed File commits and every unchanged source leaf."""
import hashlib
import json
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.test_stopped_retirement import prepared
from uuid import UUID, uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.retirement_checkpoint import verify_retirement_checkpoint
from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof


async def retired(tmp_path, *, profile='empty'):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_target_import import paid_history
    service, signer, namespace, target = await setup(tmp_path, joint=profile == 'segmented',
                                                     before_stage=paid_history if profile == 'paid' else None)
    try:
        if profile == 'segmented':
            from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
            await service.store._call(service.joint._recovery.capital.journal.migrate_archive, capacity())
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
        source = await service.store.frozen_configuration_source(service.store.binding)
        with pytest.raises(StorageError, match='NOT_COMMITTED'):
            await service.store.stopped_retirement_checkpoint(service.store.binding)
        await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        raw = await service.store.stopped_retirement_checkpoint(service.store.binding)
        body = json.loads(StoppedRetirementProof.from_json(command.postgres_retirement_json).body_json)
        args = dict(expected_sha256=hashlib.sha256(raw).hexdigest(), target_binding=namespace,
                    target_account_id=UUID(body['target_account_id']), database_lineage=UUID(body['database_lineage']),
                    target_manifest_digest=command.target_manifest_digest, max_bytes=8 * 2**20)
        return source, raw, args
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('profile', ['empty', 'paid', 'segmented'])
async def test_cold_target_verifies_exact_retirement_without_source_io_or_grant(tmp_path, monkeypatch, profile):
    source, raw, args = await retired(tmp_path, profile=profile)

    def no_io(*args, **kwargs):
        raise AssertionError('source paths must not be opened')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_io)
        report = verify_retirement_checkpoint(source, raw, **args)
    assert not report.trading_permission
    assert report.json()['checkpoint_sha256'] == args['expected_sha256']
    assert report.json()['target_account_id'] == str(args['target_account_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['money', 'journal', 'operation', 'ancestry', 'policy', 'time', 'grant_cut',
                                   'consume_cut', 'removed_consumption', 'extra_record', 'signature', 'target_account', 'target_database'])
async def test_rehashed_retirement_corruption_or_other_target_is_refused(tmp_path, fault):
    source, raw, args = await retired(tmp_path, profile='paid')
    value = json.loads(raw)
    state = value['state']
    resources = state['resources']
    if fault == 'money':
        resources['capital']['highwater_usdt'] = '99999'
    elif fault == 'journal':
        resources['journal_hashes']['loss'] = '0' * 64
    elif fault == 'operation':
        state['operations']['control:consume-pg']['revision'] -= 1
    elif fault == 'ancestry':
        next(iter(resources['commands'].values()))['command']['quantity_base'] = '99'
    elif fault == 'policy':
        value['policy']['binding']['account_uid'] = '99999'
    elif fault == 'time':
        state['checked_at'] = '2099-01-01T00:00:00+00:00'
    elif fault in ('grant_cut', 'consume_cut'):
        name = 'postgres-cut' if fault == 'grant_cut' else 'consume-pg'
        resources['control_operations'][name]['cut']['checkpoint']['state_digest'] = '0' * 64
    elif fault == 'removed_consumption':
        del resources['control_operations']['consume-pg']
    elif fault == 'extra_record':
        resources['control_operations']['unexpected'] = resources['control_operations']['consume-pg']
    elif fault == 'signature':
        resources['control_operations']['consume-pg']['signature'] = '0' * 128
    elif fault == 'target_account':
        args['target_account_id'] = uuid4()
    else:
        args['database_lineage'] = uuid4()
    raw = encode(value)
    args['expected_sha256'] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(StorageError):
        verify_retirement_checkpoint(source, raw, **args)

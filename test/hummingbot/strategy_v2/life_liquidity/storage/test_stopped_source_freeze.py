"""Signed clean PG source freeze keeps old financial state and blocks writers."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration_service import sign
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import LIMITS, maintenance, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_consumption import consuming

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.handoff import AuthorizeConfigurationHandoff
from hummingbot.strategy_v2.life_liquidity.storage.service_source import StoppedFileServiceSource


async def proposal(service, namespace, target):
    return await service.control.propose_configuration_handoff(operation_id='postgres-cut', target_binding=namespace,
                                                               target_session_json=encode(target.to_dict()).decode(),
                                                               source_format='STOPPED_FILE_SERVICE_V1', **LIMITS)


@pytest.mark.asyncio
@pytest.mark.parametrize('segmented', [False, True])
async def test_stopped_pg_source_freezes_and_reads_cold_without_becoming_a_file_target(tmp_path, segmented):
    from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
    service, signer, namespace, target = await setup(tmp_path, joint=segmented)
    cold = None
    try:
        if segmented:
            journal = service.joint._recovery.capital.journal
            await service.store._call(journal.migrate_archive, capacity())
        command, snapshot = await proposal(service, namespace, target)
        approval = sign(service, signer, command, snapshot, 'HANDOFF')
        assert AuthorizeConfigurationHandoff.from_json(command.json()) == command
        assert command.json()['source_format'] == 'STOPPED_FILE_SERVICE_V1'
        # The preexisting signed format remains byte-for-byte compatible.
        legacy = replace(command, source_format='FILE_SERVICE_SCOPED')
        assert 'source_format' not in legacy.json()
        with pytest.raises(StorageError):
            await service.control.apply(legacy, approval=approval, snapshot=snapshot)
        before = await service.store.snapshot(service.store.binding)
        receipt = await service.control.apply(command, approval=approval, snapshot=snapshot)
        assert receipt.state == 'SOURCE_FROZEN' and not receipt.replayed
        source = await service.store.frozen_configuration_source(service.store.binding)
        assert type(source) is StoppedFileServiceSource and bool(source.archives) is segmented
        assert source.content_digest == command.source_digest
        from hummingbot.strategy_v2.life_liquidity.storage.target_import import validate_manifest
        with pytest.raises(StorageError, match='POSTGRES_CUTOVER'):
            validate_manifest(None, command, source)
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await service.store._call(lambda: None)
        after = await service.store.snapshot(service.store.binding)
        assert (after.balance, after.budget, after.session) == (before.balance, before.budget, before.session)
        consume, signed, cut = await consuming(service, signer, command, snapshot)
        # A File-only consumption cannot bypass the separate PG cutover proof.
        with pytest.raises(StorageError, match='POSTGRES_CUTOVER'):
            await service.store.consume_configuration_handoff(consume, approval=signed, facts=snapshot.facts, cut=cut)
        await service.close()
        cold = await maintenance(service, signer)
        assert cold._cold_unarmed and cold._handoff_frozen
        assert await cold.frozen_configuration_source(cold.binding) == source
        assert (await cold.authorize_configuration_handoff(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)).replayed
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_pg_freeze_commit_is_resolved_by_original_cold_authority(tmp_path, monkeypatch, committed):
    service, signer, namespace, target = await setup(tmp_path)
    command, snapshot = await proposal(service, namespace, target)
    approval = sign(service, signer, command, snapshot, 'HANDOFF')
    original = service.store._state.commit

    def lost(state):
        if committed:
            original(state)
        raise OSError('lost freeze reply')
    with monkeypatch.context() as patch:
        patch.setattr(service.store._state, 'commit', lost)
        with pytest.raises(CommitUnknown):
            await service.control.apply(command, approval=approval, snapshot=snapshot)
    await service.close()
    cold = await maintenance(service, signer)
    try:
        state = await cold.configuration_handoff(cold.binding)
        assert (state.active == command) is committed
        if committed:
            assert (await cold.frozen_configuration_source(cold.binding)).content_digest == command.source_digest
        else:
            assert state.active is None
    finally:
        await cold.close()

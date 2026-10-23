"""Independent exclusion plus operator approval irreversibly retire one File cut."""
import hashlib
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration_service import sign
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import maintenance, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.test_stopped_source_freeze import proposal
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.handoff import (
    CancelConfigurationHandoff,
    ConsumeConfigurationHandoff,
)
from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import pack_stopped_source
from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof


async def prepared(service, signer, namespace, target, *, fault=None, target_request_builder=None):
    grant, snapshot = await proposal(service, namespace, target)
    await service.control.apply(grant, approval=sign(service, signer, grant, snapshot, 'HANDOFF'), snapshot=snapshot)
    source = await service.store.frozen_configuration_source(service.store.binding)
    raw = pack_stopped_source(source, max_bytes=grant.max_bytes, max_rows=grant.max_rows)
    now = service.sources.clock()
    frozen_cut, frozen_checked_at = await service.store.stopped_retirement_context(service.store.binding)
    body = dict(protocol='LIFE_OFFLINE_STOPPED_RETIREMENT_V1', provider_id='independent-retirement',
                operation_id='consume-pg', handoff_operation_id=grant.operation_id,
                source_binding=service.store.binding.json(), source_owner=grant.owner.json(),
                source_digest=source.content_digest, artifact_sha256=hashlib.sha256(raw).hexdigest(),
                target_manifest_digest='c' * 64, target_binding=namespace.json(),
                target_account_id=str(uuid4()), database_lineage=str(uuid4()),
                retired_credential_id='old-key-version', replacement_credential_id='new-key-version',
                observed_at=now.isoformat(), expires_at=(now + timedelta(milliseconds=500)).isoformat(),
                rest_denied=True, ws_denied=True, caa_disabled=True, inflight_reconciled=True,
                permanently_retired=True, unresolved_request_ids=[])
    body.update(frozen_state_sha256=frozen_cut.checkpoint.state_digest, frozen_checked_at=frozen_checked_at)
    if target_request_builder is not None:
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
        request = target_request_builder(raw)
        body.update(target_account_id=str(request.account_id), database_lineage=str(request.database_lineage),
                    target_manifest_digest=fingerprint(request.json()))
    manifest = body['target_manifest_digest']
    if fault in ('rest_denied', 'ws_denied', 'caa_disabled', 'inflight_reconciled', 'permanently_retired'):
        body[fault] = False
    elif fault == 'artifact':
        body['artifact_sha256'] = '0' * 64
    elif fault == 'binding':
        body['target_binding'] = replace(namespace, lineage=uuid4()).json()
    elif fault == 'manifest':
        body['target_manifest_digest'] = 'd' * 64
    elif fault == 'unresolved':
        body['unresolved_request_ids'] = ['accepted-unknown']
    elif fault == 'same_key':
        body['replacement_credential_id'] = body['retired_credential_id']
    elif fault == 'future':
        body['observed_at'] = (now + timedelta(milliseconds=100)).isoformat()
    elif fault == 'expired':
        body['observed_at'] = (now - timedelta(seconds=2)).isoformat()
        body['expires_at'] = now.isoformat()
    key = Ed25519PrivateKey.from_private_bytes(signer.keys['operator']) if fault == 'operator_key' else Ed25519PrivateKey.generate()
    proof = StoppedRetirementProof(encode(body).decode(), key.public_key().public_bytes_raw(), key.sign(encode(body)).hex())
    if fault == 'signature':
        proof = replace(proof, signature='0' * 128)
    command = ConsumeConfigurationHandoff('consume-pg', 1, grant.owner, grant.operation_id, manifest,
                                          postgres_retirement_json=proof.json())
    cut = (await service.store.snapshot(service.store.binding)).cut
    approval = signer.sign('operator', service.store.binding, 'HANDOFF', command.json(), cut, snapshot.facts,
                           issued_at=now, expires_at=now + timedelta(seconds=1))
    return command, approval, cut, snapshot.facts, grant


@pytest.mark.asyncio
@pytest.mark.parametrize('segmented', [False, True])
async def test_native_retirement_is_exact_durable_and_cannot_be_cancelled_or_retargeted(tmp_path, segmented):
    service, signer, namespace, target = await setup(tmp_path, joint=segmented)
    cold = None
    try:
        if segmented:
            from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
            await service.store._call(service.joint._recovery.capital.journal.migrate_archive, capacity())
        command, approval, cut, facts, grant = await prepared(service, signer, namespace, target)
        before = await service.store.snapshot(service.store.binding)
        result = await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        assert result.state == 'SOURCE_CONSUMED' and not result.replayed
        after = await service.store.snapshot(service.store.binding)
        assert (after.balance, after.budget, after.session) == (before.balance, before.budget, before.session)
        assert ConsumeConfigurationHandoff.from_json(command.json()) == command
        await service.close()
        cold = await maintenance(service, signer)
        assert (await cold.configuration_handoff(cold.binding)).consumed == command
        assert (await cold.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)).replayed
        cancel = CancelConfigurationHandoff('cannot-reverse', 2, grant.owner, grant.operation_id)
        signed = signer.sign('operator', cold.binding, 'HANDOFF', cancel.json(), after.cut, facts,
                             issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
        with pytest.raises(StorageError, match='ALREADY_CONSUMED'):
            await cold.cancel_configuration_handoff(cancel, approval=signed, facts=facts, cut=after.cut)
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await cold.consume_configuration_handoff(replace(command, target_manifest_digest='f' * 64), approval=approval, facts=facts, cut=cut)
        assert cold._cold_unarmed and cold._handoff_frozen
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['rest_denied', 'ws_denied', 'caa_disabled', 'inflight_reconciled', 'permanently_retired',
                                   'artifact', 'binding', 'manifest', 'unresolved', 'same_key', 'future', 'expired', 'signature', 'operator_key'])
async def test_unqualified_exclusion_cannot_consume_source_even_with_operator_signature(tmp_path, fault):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target, fault=fault)
        with pytest.raises(StorageError, match='STOPPED_RETIREMENT'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is None
        assert (await service.store.snapshot(service.store.binding)).cut == cut
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('published', [False, True])
async def test_native_retirement_lost_commit_resolves_without_another_target(tmp_path, monkeypatch, published):
    service, signer, namespace, target = await setup(tmp_path)
    command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
    original = service.store._state.commit

    def lost(state):
        if published:
            original(state)
        raise OSError('synthetic lost retirement reply')
    with monkeypatch.context() as patch:
        patch.setattr(service.store._state, 'commit', lost)
        with pytest.raises(CommitUnknown):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
    await service.close()
    cold = await maintenance(service, signer)
    try:
        assert ((await cold.configuration_handoff(cold.binding)).consumed == command) is published
        assert (await cold.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)).replayed is published
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_retirement_expiring_at_physical_commit_keeps_source_reversible(tmp_path, monkeypatch):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
        original = service.store._control_commit
        now = service.sources.clock()

        def late(*args, **kwargs):
            service.store._clock = lambda: now + timedelta(milliseconds=500)
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', late)
        with pytest.raises(StorageError, match='STOPPED_RETIREMENT'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_proof_replacement_needs_new_operator_approval(tmp_path):
    import json
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
        proof = StoppedRetirementProof.from_json(command.postgres_retirement_json)
        body = json.loads(proof.body_json)
        body['database_lineage'] = str(uuid4())
        replacement = Ed25519PrivateKey.generate()
        new = StoppedRetirementProof(encode(body).decode(), replacement.public_key().public_bytes_raw(), replacement.sign(encode(body)).hex())
        command = replace(command, postgres_retirement_json=new.json())
        with pytest.raises(StorageError, match='OPERATOR_APPROVAL_UNQUALIFIED'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_expired_historical_retirement_remains_readable_but_never_unfreezes(tmp_path):
    service, signer, namespace, target = await setup(tmp_path)
    cold = None
    try:
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
        await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        now = service.sources.clock()
        await service.close()
        service.sources = replace(service.sources, clock=lambda: now + timedelta(days=1))
        cold = await maintenance(service, signer)
        assert (await cold.configuration_handoff(cold.binding)).consumed == command
        assert (await cold.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)).replayed
        with pytest.raises(StorageError, match='SOURCE_FROZEN'):
            await cold._call(lambda: None)
    finally:
        if cold is not None:
            await cold.close()
        await service.close()


@pytest.mark.asyncio
async def test_exclusion_expiry_during_physical_source_hashing_does_not_retire(tmp_path, monkeypatch):
    service, signer, namespace, target = await setup(tmp_path)
    try:
        command, approval, cut, facts, _ = await prepared(service, signer, namespace, target)
        original, original_hashes = service.store._control_commit, service.store._hashes
        now = service.sources.clock()
        committing = False

        def hashes(*args):
            result = original_hashes(*args)
            if committing:
                service.store._clock = lambda: now + timedelta(milliseconds=500)
            return result

        def commit(*args, **kwargs):
            nonlocal committing
            committing = True
            return original(*args, **kwargs)
        monkeypatch.setattr(service.store, '_control_commit', commit)
        monkeypatch.setattr(service.store, '_hashes', hashes)
        with pytest.raises(StorageError, match='STOPPED_RETIREMENT'):
            await service.store.consume_configuration_handoff(command, approval=approval, facts=facts, cut=cut)
        assert (await service.store.configuration_handoff(service.store.binding)).consumed is None
    finally:
        await service.close()

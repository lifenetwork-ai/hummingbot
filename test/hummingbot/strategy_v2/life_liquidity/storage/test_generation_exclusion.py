"""Pinned credential-authority assertions; synthetic keys and offline scope."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_runtime_config import configuration
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import (
    CredentialExclusion,
    CredentialExclusionVerifier,
    GenerationIdentity,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessCapability


def proof(*, cfg=None, source=None, target=None, grant=None, epoch=1, private=None):
    cfg = cfg or configuration()
    now = datetime.now(timezone.utc)
    source = source or OwnerToken(uuid4(), uuid4(), 1, uuid4(), now - timedelta(seconds=2), now + timedelta(seconds=30))
    target = target or replace(source, process_id=uuid4(), generation=uuid4())
    capability = ProcessCapability.create(cfg, target)
    private = private or Ed25519PrivateKey.generate()
    verifier = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    cert = CredentialExclusion('synthetic-provider', cfg.binding, cfg.database_lineage, grant or uuid4(), epoch,
                               GenerationIdentity(source, 'service:old', 'secret:old:1', '1' * 64),
                               GenerationIdentity(target, 'service:new', 'secret:new:1', '1' * 64),
                               capability.registration.capability_hash,
                               now - timedelta(seconds=1), now - timedelta(milliseconds=500), now,
                               now + timedelta(seconds=5), (), True, True, True, True, '0' * 128)
    cert = sign(cert, private)
    return cfg, source, capability, private, verifier, cert, now


def sign(cert, private):
    return replace(cert, signature=private.sign(encode(cert.body())).hex())


def verify(verifier, cert, cfg, source, capability, now):
    return verifier.verify(cert, binding=cfg.binding, database_lineage=cfg.database_lineage,
                           predecessor_grant=cert.predecessor_grant, predecessor_epoch=cert.predecessor_epoch,
                           predecessor_owner=source, target_registration=capability.registration, now=now)


def test_valid_same_revision_replacement_is_bound_to_new_private_process_and_distinct_credentials():
    cfg, old, cap, _, verifier, cert, now = proof()
    assert cert.source.image_digest == cert.target.image_digest
    verify(verifier, cert, cfg, old, cap, now)
    assert 'signature' not in cert.body() and cert.signature not in repr(cert)
    assert cap._secret not in repr(cert) and cap._secret not in str(cert.body())
    assert verifier.policy()['protocol'] == 'OFFLINE_FAKE_V1'


@pytest.mark.parametrize('fault', ['rest', 'ws', 'caa', 'inflight', 'future', 'expired', 'identity', 'resource',
                                   'process', 'generation', 'capability', 'lineage', 'binding', 'signature', 'provider'])
def test_incomplete_foreign_or_delayed_exclusion_cannot_authorize_successor(fault):
    cfg, old, cap, private, verifier, cert, now = proof()
    if fault in ('rest', 'ws', 'caa', 'inflight'):
        cert = replace(cert, **{dict(rest='rest_denied', ws='ws_denied', caa='caa_disabled', inflight='inflight_reconciled')[fault]: False})
    elif fault == 'future':
        now = cert.observed_at - timedelta(milliseconds=1)
    elif fault == 'expired':
        now = cert.expires_at
    elif fault == 'identity':
        cert = replace(cert, target=replace(cert.target, service_identity=cert.source.service_identity))
    elif fault == 'resource':
        cert = replace(cert, target=replace(cert.target, credential_resource=cert.source.credential_resource))
    elif fault == 'process':
        cert = replace(cert, target=replace(cert.target, owner=replace(cap._owner, process_id=old.process_id)))
    elif fault == 'generation':
        cert = replace(cert, target=replace(cert.target, owner=replace(cap._owner, generation=old.generation)))
    elif fault == 'capability':
        cert = replace(cert, target_capability_hash='f' * 64)
    elif fault == 'lineage':
        cert = replace(cert, database_lineage=uuid4())
    elif fault == 'binding':
        cert = replace(cert, binding=replace(cfg.binding, config_hash='d' * 64))
    elif fault == 'provider':
        cert = replace(cert, provider_id='foreign-provider')
    cert = sign(cert, private) if fault != 'signature' else replace(cert, signature='0' * 128)
    with pytest.raises(StorageError):
        verify(verifier, cert, cfg, old, cap, now)


@pytest.mark.parametrize('environment', ['DEMO', 'PRODUCTION'])
def test_offline_authority_assertion_cannot_be_relabelled_for_external_accounts(environment):
    cfg, old, cap, private, verifier, cert, now = proof()
    with pytest.raises(StorageError):
        cert = sign(replace(cert, binding=replace(cfg.binding, environment=environment)), private)
        verify(verifier, cert, cfg, old, cap, now)


@pytest.mark.parametrize('fault', ['registration_lineage', 'future_owner'])
def test_exclusion_also_requires_original_target_registration_and_current_owner_window(fault):
    cfg, old, cap, private, verifier, cert, now = proof()
    registration = cap.registration
    if fault == 'registration_lineage':
        registration = replace(registration, database_lineage=uuid4())
    else:
        target = replace(cert.target.owner, valid_from=now + timedelta(seconds=1))
        registration = replace(registration, owner=target)
        cert = sign(replace(cert, target=replace(cert.target, owner=target)), private)
    with pytest.raises(StorageError, match='EXCLUSION_UNQUALIFIED'):
        verifier.verify(cert, binding=cfg.binding, database_lineage=cfg.database_lineage,
                        predecessor_grant=cert.predecessor_grant, predecessor_epoch=cert.predecessor_epoch,
                        predecessor_owner=old, target_registration=registration, now=now)

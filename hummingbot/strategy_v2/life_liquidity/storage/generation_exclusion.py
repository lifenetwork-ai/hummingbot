"""Signed offline credential-exclusion assertions; no secrets or key issuer.

The separately pinned credential authority must establish REST/WS revocation,
CAA exclusion and accepted/in-flight outcomes before signing. This verifier
cannot infer those facts from a lease or turn a mock into demo/live evidence.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageError,
    digest,
    encode,
    identifier,
    utc,
)


@dataclass(frozen=True)
class GenerationIdentity:
    owner: OwnerToken
    service_identity: str
    credential_resource: str
    image_digest: str

    def __post_init__(self):
        if type(self.owner) is not OwnerToken:
            raise StorageError('GENERATION_IDENTITY_INVALID')
        identifier(self.service_identity)
        identifier(self.credential_resource)
        digest(self.image_digest)

    def json(self):
        return dict(owner=self.owner.json(), service_identity=self.service_identity,
                    credential_resource=self.credential_resource, image_digest=self.image_digest)


@dataclass(frozen=True)
class CredentialExclusion:
    provider_id: str
    binding: StateBinding
    database_lineage: UUID
    predecessor_grant: UUID
    predecessor_epoch: int
    source: GenerationIdentity
    target: GenerationIdentity
    target_capability_hash: str
    revocation_requested_at: datetime
    revocation_effective_at: datetime
    observed_at: datetime
    expires_at: datetime
    accepted_request_ids: tuple[str, ...]
    rest_denied: bool
    ws_denied: bool
    caa_disabled: bool
    inflight_reconciled: bool
    signature: str = field(repr=False)

    def __post_init__(self):
        identifier(self.provider_id)
        if (type(self.binding) is not StateBinding or self.binding.environment != 'OFFLINE_SIMULATION'
                or any(not isinstance(v, UUID) or not v.int for v in (self.database_lineage, self.predecessor_grant))
                or type(self.predecessor_epoch) is not int or not 0 < self.predecessor_epoch < 2**63 - 1
                or type(self.source) is not GenerationIdentity or type(self.target) is not GenerationIdentity
                or type(self.accepted_request_ids) is not tuple or len(self.accepted_request_ids) > 4096
                or any(type(v) is not bool for v in (self.rest_denied, self.ws_denied, self.caa_disabled, self.inflight_reconciled))
                or type(self.signature) is not str or re.fullmatch('[0-9a-f]{128}', self.signature) is None):
            raise StorageError('CREDENTIAL_EXCLUSION_INVALID')
        digest(self.target_capability_hash)
        for value in self.accepted_request_ids:
            identifier(value)
        if tuple(sorted(set(self.accepted_request_ids))) != self.accepted_request_ids:
            raise StorageError('CREDENTIAL_EXCLUSION_REQUEST_SET_INVALID')
        for value in (self.revocation_requested_at, self.revocation_effective_at, self.observed_at, self.expires_at):
            utc(value)
        if not self.revocation_requested_at <= self.revocation_effective_at <= self.observed_at < self.expires_at:
            raise StorageError('CREDENTIAL_EXCLUSION_TIME_INVALID')

    def body(self):
        return dict(protocol='OFFLINE_FAKE_V1', provider_id=self.provider_id, binding=self.binding.json(),
                    database_lineage=str(self.database_lineage), predecessor_grant=str(self.predecessor_grant),
                    predecessor_epoch=self.predecessor_epoch, source=self.source.json(), target=self.target.json(),
                    target_capability_hash=self.target_capability_hash,
                    revocation_requested_at=self.revocation_requested_at.isoformat(),
                    revocation_effective_at=self.revocation_effective_at.isoformat(),
                    observed_at=self.observed_at.isoformat(), expires_at=self.expires_at.isoformat(),
                    accepted_request_ids=list(self.accepted_request_ids), rest_denied=self.rest_denied,
                    ws_denied=self.ws_denied, caa_disabled=self.caa_disabled, inflight_reconciled=self.inflight_reconciled)


@dataclass(frozen=True)
class CredentialExclusionVerifier:
    provider_id: str
    public_key: bytes = field(repr=False)

    def __post_init__(self):
        identifier(self.provider_id)
        if type(self.public_key) is not bytes or len(self.public_key) != 32:
            raise StorageError('CREDENTIAL_AUTHORITY_KEY_INVALID')

    def policy(self):
        return dict(protocol='OFFLINE_FAKE_V1', provider_id=self.provider_id,
                    public_key_sha256=hashlib.sha256(self.public_key).hexdigest())

    def verify(self, certificate, *, binding, database_lineage, predecessor_grant, predecessor_epoch,
               predecessor_owner, target_registration, now):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessRegistration
        c = certificate
        if (type(c) is not CredentialExclusion or type(target_registration) is not ProcessRegistration
                or c.provider_id != self.provider_id or c.binding != binding or binding.environment != 'OFFLINE_SIMULATION'
                or c.database_lineage != database_lineage or c.predecessor_grant != predecessor_grant
                or target_registration.database_lineage != database_lineage
                or c.predecessor_epoch != predecessor_epoch or c.source.owner != predecessor_owner
                or c.target.owner != target_registration.owner or c.target_capability_hash != target_registration.capability_hash
                or c.target.owner.process_id == predecessor_owner.process_id
                or c.target.owner.generation == predecessor_owner.generation
                or c.source.service_identity == c.target.service_identity
                or c.source.credential_resource == c.target.credential_resource
                or not all((c.rest_denied, c.ws_denied, c.caa_disabled, c.inflight_reconciled))
                or not c.target.owner.valid_from <= utc(now) < c.target.owner.expires_at
                or not c.observed_at <= utc(now) < c.expires_at <= c.target.owner.expires_at):
            raise StorageError('CREDENTIAL_EXCLUSION_UNQUALIFIED')
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(bytes.fromhex(c.signature), encode(c.body()))
        except (InvalidSignature, ValueError, TypeError):
            raise StorageError('CREDENTIAL_EXCLUSION_SIGNATURE_INVALID') from None

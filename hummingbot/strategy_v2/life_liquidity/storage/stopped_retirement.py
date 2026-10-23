"""Independent offline credential exclusion, bound to one signed File handoff.

The HANDOFF operator explicitly pins the authority public key and exact target
in the signed consumption command. A separate authority signs exclusion facts.
Neither signature is an exchange revocation operation or native activation.
"""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, digest, encode, identifier, utc


@dataclass(frozen=True)
class StoppedRetirementProof:
    body_json: str
    public_key: bytes = field(repr=False)
    signature: str = field(repr=False)

    def __post_init__(self):
        try:
            if (type(self.body_json) is not str or len(self.body_json.encode()) > 16384
                    or type(self.public_key) is not bytes or len(self.public_key) != 32
                    or type(self.signature) is not str or re.fullmatch('[0-9a-f]{128}', self.signature) is None):
                raise ValueError()
            body = decode(self.body_json)
            if (type(body) is not dict or encode(body).decode() != self.body_json or set(body) != {
                    'protocol', 'provider_id', 'operation_id', 'handoff_operation_id', 'source_binding', 'source_owner',
                    'source_digest', 'artifact_sha256', 'target_manifest_digest', 'target_binding', 'target_account_id',
                    'database_lineage', 'retired_credential_id', 'replacement_credential_id', 'observed_at', 'expires_at',
                    'rest_denied', 'ws_denied', 'caa_disabled', 'inflight_reconciled', 'permanently_retired', 'unresolved_request_ids',
                    'frozen_checked_at', 'frozen_state_sha256'}):
                raise ValueError()
            for key in ('provider_id', 'operation_id', 'handoff_operation_id', 'retired_credential_id', 'replacement_credential_id'):
                identifier(body[key])
            for key in ('source_digest', 'artifact_sha256', 'target_manifest_digest', 'frozen_state_sha256'):
                digest(body[key])
            for key in ('target_account_id', 'database_lineage'):
                if not UUID(body[key]).int or str(UUID(body[key])) != body[key]:
                    raise ValueError()
            for key in ('observed_at', 'expires_at', 'frozen_checked_at'):
                utc(datetime.fromisoformat(body[key]))
        except (StorageError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise StorageError('STOPPED_RETIREMENT_ENCODING_INVALID') from None

    def json(self):
        return encode(dict(body=decode(self.body_json), public_key=self.public_key.hex(), signature=self.signature)).decode()

    @classmethod
    def from_json(cls, raw):
        try:
            if type(raw) is not str or len(raw.encode()) > 18000:
                raise ValueError()
            value = decode(raw)
            if set(value) != {'body', 'public_key', 'signature'} or encode(value).decode() != raw:
                raise ValueError()
            return cls(encode(value['body']).decode(), bytes.fromhex(value['public_key']), value['signature'])
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise StorageError('STOPPED_RETIREMENT_ENCODING_INVALID') from None

    def verify(self, command, grant, binding, *, operator_keys=(), facts=None, source=None, now=None, cut=None):
        b = decode(self.body_json)
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(bytes.fromhex(self.signature), self.body_json.encode())
        except (InvalidSignature, ValueError, TypeError):
            raise StorageError('STOPPED_RETIREMENT_SIGNATURE_INVALID') from None
        if (grant.source_format != 'STOPPED_FILE_SERVICE_V1' or binding.environment != 'OFFLINE_SIMULATION'
                or b['protocol'] != 'LIFE_OFFLINE_STOPPED_RETIREMENT_V1' or self.public_key in operator_keys
                or b['operation_id'] != command.operation_id or b['handoff_operation_id'] != grant.operation_id
                or b['source_binding'] != binding.json() or b['source_owner'] != grant.owner.json()
                or b['source_digest'] != grant.source_digest or b['target_binding'] != grant.target_binding.json()
                or b['target_manifest_digest'] != command.target_manifest_digest
                or b['retired_credential_id'] == b['replacement_credential_id']
                or any(b[key] is not True for key in ('rest_denied', 'ws_denied', 'caa_disabled', 'inflight_reconciled', 'permanently_retired'))
                or b['unresolved_request_ids'] != []):
            raise StorageError('STOPPED_RETIREMENT_SCOPE_UNQUALIFIED')
        observed, expires = (datetime.fromisoformat(b[key]) for key in ('observed_at', 'expires_at'))
        if not observed < expires or (expires - observed).total_seconds() > 60:
            raise StorageError('STOPPED_RETIREMENT_TIME_UNQUALIFIED')
        if now is not None and not observed <= utc(now) < expires:
            raise StorageError('STOPPED_RETIREMENT_EXPIRED_OR_FUTURE')
        if cut is not None and b['frozen_state_sha256'] != cut.checkpoint.state_digest:
            raise StorageError('STOPPED_RETIREMENT_CUT_UNQUALIFIED')
        if facts is not None and (facts.binding != binding or facts.observed_at < observed or facts.accepted_wire_ids
                                  or facts.runner_wire_ids or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                                  or not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))):
            raise StorageError('STOPPED_RETIREMENT_FACTS_UNQUALIFIED')
        if source is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import pack_stopped_source
            raw = pack_stopped_source(source, max_bytes=grant.max_bytes, max_rows=grant.max_rows)
            if (hashlib.sha256(raw).hexdigest() != b['artifact_sha256'] or source.content_digest != grant.source_digest
                    or source.base.core.facts != facts):
                raise StorageError('STOPPED_RETIREMENT_ARTIFACT_UNQUALIFIED')
        return b

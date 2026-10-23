"""Public-key verified offline audit/backup diagnostics, never send authority.

Private signing keys belong to the independent maintenance process. The worker
accepts bounded attestations for its exact namespace and does not infer freshness
from a local SQL signature column, file timestamp or a successful provider poll.
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, encode

KINDS = ('AUDIT', 'BACKUP', 'RESTORE')


@dataclass(frozen=True)
class IntegrityReceipt:
    body_json: str
    signature: str = field(repr=False)

    def __post_init__(self):
        try:
            if (type(self.body_json) is not str or len(self.body_json.encode()) > 8192
                    or type(self.signature) is not str or re.fullmatch('[0-9a-f]{128}', self.signature) is None):
                raise ValueError()
            body = decode(self.body_json)
            if (type(body) is not dict or set(body) != {'protocol', 'publisher_id', 'account_id', 'database_lineage',
                                                        'binding', 'kind', 'observed_at', 'result', 'artifact_digest'}
                    or encode(body).decode() != self.body_json or body['kind'] not in KINDS
                    or type(body['observed_at']) is not str
                    or type(body['artifact_digest']) is not str or re.fullmatch('[0-9a-f]{64}', body['artifact_digest']) is None):
                raise ValueError()
            stamp = datetime.fromisoformat(body['observed_at'])
            if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('INTEGRITY_RECEIPT_INVALID') from None

    @property
    def body(self):
        return decode(self.body_json)


@dataclass(frozen=True)
class IntegrityPolicy:
    publisher_id: str
    public_key: bytes = field(repr=False)
    account_id: UUID
    database_lineage: UUID
    binding: StateBinding
    audit_max_age_ms: int
    backup_max_age_ms: int
    restore_max_age_ms: int

    def __post_init__(self):
        if (type(self.publisher_id) is not str or re.fullmatch('[A-Za-z0-9_.:-]{1,128}', self.publisher_id) is None
                or type(self.public_key) is not bytes or len(self.public_key) != 32
                or any(type(v) is not UUID or not v.int for v in (self.account_id, self.database_lineage))
                or type(self.binding) is not StateBinding or self.binding.environment != 'OFFLINE_SIMULATION'
                or any(type(v) is not int or not 1 <= v <= 7 * 86400000 for v in (
                    self.audit_max_age_ms, self.backup_max_age_ms, self.restore_max_age_ms))):
            raise ValueError('INTEGRITY_POLICY_INVALID')

    def verify(self, receipt, *, now):
        if type(receipt) is not IntegrityReceipt or type(now) is not datetime or now.tzinfo is None:
            raise ValueError('INTEGRITY_RECEIPT_UNQUALIFIED')
        body = receipt.body
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(bytes.fromhex(receipt.signature), receipt.body_json.encode())
        except (InvalidSignature, TypeError, ValueError):
            raise ValueError('INTEGRITY_SIGNATURE_INVALID') from None
        if (body['protocol'] != 'LIFE_OFFLINE_INTEGRITY_V1' or body['publisher_id'] != self.publisher_id
                or body['account_id'] != str(self.account_id) or body['database_lineage'] != str(self.database_lineage)
                or body['binding'] != self.binding.json() or body['result'] != 'PASSED'):
            raise ValueError('INTEGRITY_RECEIPT_SCOPE_OR_RESULT_INVALID')
        age = (now - datetime.fromisoformat(body['observed_at'])).total_seconds() * 1000
        if not 0 <= age <= dict(AUDIT=self.audit_max_age_ms, BACKUP=self.backup_max_age_ms, RESTORE=self.restore_max_age_ms)[body['kind']]:
            raise ValueError('INTEGRITY_RECEIPT_STALE')
        return body


class IntegrityMonitor:
    def __init__(self, policy, provider, *, timeout_ms, clock=None):
        if (type(policy) is not IntegrityPolicy or not callable(provider)
                or type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000):
            raise ValueError('INTEGRITY_MONITOR_CONFIG_INVALID')
        self.policy, self._provider, self._timeout_ms = policy, provider, timeout_ms
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._task, self._closed, self._busy = None, False, False
        self._receipts, self._seen, self._state = {}, {}, 'NOT_SAMPLED'

    async def sample(self):
        if self._closed or self._busy:
            return
        if self._task is not None:
            if not self._task.done():
                return
            # Late completion after a failed poll does not refresh health.
            if not self._task.cancelled():
                self._task.exception()
            self._task = None
        self._busy = True
        try:
            self._task = asyncio.create_task(self._provider())
            done, _ = await asyncio.wait((self._task,), timeout=self._timeout_ms / 1000)
            if not done:
                raise ValueError('INTEGRITY_PROVIDER_TIMEOUT')
            if self._task.cancelled():
                raise ValueError('INTEGRITY_PROVIDER_CANCELLED')
            values = self._task.result()
            if type(values) is not tuple or len(values) > len(KINDS):
                raise ValueError('INTEGRITY_PROVIDER_INVALID')
            receipts, seen = {}, dict(self._seen)
            now = self._clock()
            for value in values:
                body = self.policy.verify(value, now=now)
                kind, stamp = body['kind'], datetime.fromisoformat(body['observed_at'])
                old = seen.get(kind)
                if (kind in receipts or old is not None and (stamp < old[0] or stamp == old[0] and value != old[1])):
                    raise ValueError('INTEGRITY_RECEIPT_ROLLBACK_OR_CONFLICT')
                receipts[kind], seen[kind] = value, (stamp, value)
            self._receipts, self._seen, self._state = receipts, seen, 'CURRENT'
        except asyncio.CancelledError:
            self._state = 'UNAVAILABLE'
            raise
        except Exception:
            self._state = 'UNAVAILABLE'
        finally:
            self._busy = False

    def status(self):
        result = dict(state='CLOSED' if self._closed else self._state, trading_permission=False)
        for kind, name in (('AUDIT', 'audit'), ('BACKUP', 'backup'), ('RESTORE', 'recovery')):
            health, age = 'UNVERIFIED', None
            try:
                if self._state == 'CURRENT' and not self._closed and kind in self._receipts:
                    now = self._clock()
                    body = self.policy.verify(self._receipts[kind], now=now)
                    health, age = 'VERIFIED', (now - datetime.fromisoformat(body['observed_at'])).total_seconds() * 1000
            except Exception:
                pass
            result[name + '_health'], result[name + '_age_ms'] = health, age
        return result

    async def close(self):
        self._closed = True
        if self._task is not None:
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                # A provider that cancelled itself is a failed observation.
                # Preserve cancellation of the caller performing the join.
                if asyncio.current_task().cancelling() or not self._task.cancelled():
                    raise
            except Exception:
                pass

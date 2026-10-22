"""Disposable immutable exact-order identities; no trading or release permit."""
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StateBinding,
    StorageError,
    digest,
    encode,
    fingerprint,
    identifier,
    utc,
)


@dataclass(frozen=True)
class CancellationIdentity:
    intent_id: str
    product: str
    instrument: str
    wire_id: str
    exchange_order_id: str
    process_id: UUID
    generation: UUID
    session_id: UUID
    epoch: int

    def __post_init__(self):
        identifier(self.intent_id)
        if (self.product not in ('SPOT', 'SWAP') or self.instrument != ('LIFE-USDT' if self.product == 'SPOT' else 'LIFE-USDT-SWAP')
                or type(self.wire_id) is not str or re.fullmatch('[A-Za-z0-9]{1,32}', self.wire_id) is None
                or type(self.exchange_order_id) is not str or re.fullmatch('[0-9]{1,128}', self.exchange_order_id) is None
                or any(not isinstance(v, UUID) or not v.int for v in (self.process_id, self.generation, self.session_id))
                or type(self.epoch) is not int or self.epoch <= 0):
            raise StorageError('CANCEL_IDENTITY_INVALID')

    def json(self):
        return dict(intent_id=self.intent_id, product=self.product, instrument=self.instrument, wire_id=self.wire_id,
                    exchange_order_id=self.exchange_order_id, process_id=str(self.process_id), generation=str(self.generation),
                    session_id=str(self.session_id), epoch=self.epoch)

    @classmethod
    def parse(cls, value):
        value = dict(value)
        for key in ('process_id', 'generation', 'session_id'):
            value[key] = UUID(value[key])
        return cls(**value)


@dataclass(frozen=True)
class CancellationManifest:
    binding: StateBinding
    database_lineage: UUID
    account_id: UUID
    version: int
    state_digest: str
    observed_at: datetime
    identities: tuple[CancellationIdentity, ...]

    def __post_init__(self):
        if (type(self.binding) is not StateBinding or self.binding.environment != 'OFFLINE_SIMULATION'
                or any(not isinstance(v, UUID) or not v.int for v in (self.database_lineage, self.account_id))
                or type(self.version) is not int or self.version < 0 or type(self.identities) is not tuple
                or len(self.identities) > 4096 or any(type(v) is not CancellationIdentity for v in self.identities)
                or len({v.wire_id for v in self.identities}) != len(self.identities)
                or len({v.exchange_order_id for v in self.identities}) != len(self.identities)):
            raise StorageError('CANCEL_MANIFEST_INVALID_OR_AMBIGUOUS')
        digest(self.state_digest)
        utc(self.observed_at)
        if len(encode(self.json())) > 1048576:
            raise StorageError('CANCEL_MANIFEST_TOO_LARGE')

    def json(self):
        return dict(binding=self.binding.json(), database_lineage=str(self.database_lineage), account_id=str(self.account_id),
                    version=self.version, state_digest=self.state_digest, observed_at=self.observed_at.isoformat(),
                    identities=[v.json() for v in self.identities])

    @property
    def content_digest(self):
        return fingerprint(self.json())

    def matches(self, request):
        if request['params'] or request['method'] != 'POST':
            return False
        try:
            body = json.loads(request['data'])
        except (ValueError, TypeError):
            return False
        if type(body) is not dict or set(body) != {'instId', 'clOrdId', 'ordId'}:
            return False
        return sum(body == dict(instId=v.instrument, clOrdId=v.wire_id, ordId=v.exchange_order_id) for v in self.identities) == 1

    def save(self, path):
        path = Path(path)
        body = encode(dict(body=self.json(), sha256=self.content_digest))
        if len(body) > 1048576:
            raise StorageError('CANCEL_MANIFEST_TOO_LARGE')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if self.load(path) != self:
                raise StorageError('CANCEL_MANIFEST_FILE_CONFLICT') from None
            return
        with os.fdopen(fd, 'wb') as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @classmethod
    def load(cls, path):
        try:
            fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 1048576:
                    raise StorageError('CANCEL_MANIFEST_FILE_INVALID')
                content = stream.read(1048577)
                if len(content) > 1048576:
                    raise StorageError('CANCEL_MANIFEST_FILE_INVALID')
            wrapper = json.loads(content)
            if type(wrapper) is not dict or set(wrapper) != {'body', 'sha256'}:
                raise StorageError('CANCEL_MANIFEST_FILE_INVALID')
            body = dict(wrapper['body'])
            body['binding'] = StateBinding.from_json(body['binding'])
            for key in ('account_id', 'database_lineage'):
                body[key] = UUID(body[key])
            body['observed_at'] = datetime.fromisoformat(body['observed_at'])
            body['identities'] = tuple(CancellationIdentity.parse(v) for v in body['identities'])
            manifest = cls(**body)
            if manifest.content_digest != wrapper['sha256']:
                raise StorageError('CANCEL_MANIFEST_FILE_INVALID')
            return manifest
        except (OSError, KeyError, TypeError, ValueError):
            raise StorageError('CANCEL_MANIFEST_FILE_INVALID') from None

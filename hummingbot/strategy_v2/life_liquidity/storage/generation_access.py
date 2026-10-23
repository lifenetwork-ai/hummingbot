"""Offline control-plane broker and signed, process-private runtime clients.

The broker/provider live outside worker memory. Runtime RPCs can read credentials,
read pinned cancellation identities and authorize individual requests. An application permit cannot revoke a cached exchange key:
provider exclusion and its accepted-request drain precede successor delivery.
No DEMO/PRODUCTION profile is accepted by this implementation.
"""
import asyncio
import hashlib
import json
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import (
    CredentialExclusion,
    CredentialExclusionVerifier,
    GenerationIdentity,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessRegistration


@dataclass(frozen=True)
class GenerationCredentials:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)

    def __post_init__(self):
        if any(type(v) is not str or not v or len(v.encode()) > 4096 for v in (self.api_key, self.secret, self.passphrase)):
            raise StorageError('GENERATION_CREDENTIAL_INVALID')


@dataclass(frozen=True)
class BrokerRegistration:
    identity: GenerationIdentity
    process: ProcessRegistration
    public_key: bytes

    def __post_init__(self):
        if (type(self.identity) is not GenerationIdentity or type(self.process) is not ProcessRegistration
                or self.identity.owner != self.process.owner or type(self.public_key) is not bytes or len(self.public_key) != 32):
            raise StorageError('GENERATION_REGISTRATION_INVALID')

    @property
    def client_id(self):
        return fingerprint(dict(identity=self.identity.json(), account_id=str(self.process.account_id),
                                database_lineage=str(self.process.database_lineage), principal=self.process.principal,
                                capability_hash=self.process.capability_hash, public_key=self.public_key.hex()))


@dataclass(frozen=True)
class BrokerCall:
    client_id: str
    sequence: int
    action: str
    payload: dict = field(repr=False)
    signature: bytes = field(repr=False)

    def body(self):
        return dict(client_id=self.client_id, sequence=self.sequence, action=self.action, payload=self.payload)


@dataclass(frozen=True)
class BrokerReply:
    body: dict = field(repr=False)
    signature: bytes = field(repr=False)


class BrokerIdentity:
    @classmethod
    def create(cls, identity, registration):
        self = cls()
        self._key, self._pid, self._sequence = Ed25519PrivateKey.generate(), os.getpid(), 0
        self.registration = BrokerRegistration(identity, registration, self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        return self

    def call(self, action, payload):
        if self._pid != os.getpid() or action not in ('FETCH', 'AUTHORIZE', 'WS_READ', 'CANCEL_MANIFEST') or type(payload) is not dict:
            raise StorageError('GENERATION_PRIVATE_IDENTITY_INVALID')
        self._sequence += 1
        call = BrokerCall(self.registration.client_id, self._sequence, action, json.loads(encode(payload)), b'')
        return BrokerCall(call.client_id, call.sequence, call.action, call.payload, self._key.sign(encode(call.body())))

    def __reduce__(self):
        raise StorageError('GENERATION_PRIVATE_IDENTITY_SERIALIZATION_DENIED')


_READS = frozenset('/api/v5/' + p for p in (
    'public/instruments', 'market/books', 'public/mark-price', 'market/index-tickers', 'public/funding-rate', 'public/position-tiers',
    'account/config', 'account/balance', 'account/positions', 'account/bills', 'account/bills-archive',
    'account/trade-fee', 'account/leverage-info', 'trade/order', 'trade/orders-pending', 'trade/orders-history',
    'trade/orders-history-archive', 'trade/orders-algo-pending', 'trade/fills', 'trade/fills-history'))
_NORMAL = frozenset('/api/v5/trade/' + p for p in ('order', 'batch-orders', 'amend-order', 'amend-batch-orders', 'cancel-order', 'cancel-batch-orders'))


def request_body(request):
    if type(request) is not RESTRequestSnapshot:
        raise StorageError('GENERATION_REQUEST_INVALID')
    headers = {k.lower(): v for k, v in request.headers}
    key = headers.get('ok-access-key', '')
    return dict(method=request.method.value, url=request.url, params=list(request.params), data=request.data,
                authenticated=request.is_auth_required, api_key_sha256=hashlib.sha256(key.encode()).hexdigest())


class GenerationBroker:
    """Trusted owner/provider side. Never serialize/share this object to workers."""

    def __init__(self, binding, database_lineage, signer, *, origins, max_generations, max_calls, provider=None):
        if (binding.environment != 'OFFLINE_SIMULATION' or not isinstance(database_lineage, UUID)
                or not isinstance(signer, Ed25519PrivateKey) or type(origins) is not tuple or not origins
                or any(type(v) is not str or urlsplit(v).scheme != 'https' or urlsplit(v).path for v in origins)
                or type(max_generations) is not int or not 1 <= max_generations <= 1024
                or type(max_calls) is not int or not 1 <= max_calls <= 1000000):
            raise StorageError('GENERATION_BROKER_CONFIG_INVALID')
        self.binding, self.database_lineage, self._signer = binding, database_lineage, signer
        self.origins, self.max_generations, self.max_calls = origins, max_generations, max_calls
        self.provider, self._generations, self._current, self._certified = provider, {}, None, {}
        self._account_id = None
        self.public_key = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

    def policy(self):
        return dict(protocol='OFFLINE_GENERATION_ACCESS_V1', public_key_sha256=hashlib.sha256(self.public_key).hexdigest(),
                    origins=list(self.origins), max_generations=self.max_generations, max_calls=self.max_calls)

    def __reduce__(self):
        raise StorageError('GENERATION_BROKER_SERIALIZATION_DENIED')

    def _entry(self, registration):
        if type(registration) is not BrokerRegistration:
            raise StorageError('GENERATION_REGISTRATION_INVALID')
        entry = self._generations.get(registration.client_id)
        if entry is None or entry['registration'] != registration:
            raise StorageError('GENERATION_REGISTRATION_UNKNOWN')
        return entry

    def stage(self, registration, credentials):
        if (type(registration) is not BrokerRegistration or type(credentials) is not GenerationCredentials
                or registration.process.database_lineage != self.database_lineage
                or self._account_id is not None and registration.process.account_id != self._account_id):
            raise StorageError('GENERATION_REGISTRATION_INVALID')
        if len(self._generations) >= self.max_generations:
            raise StorageError('GENERATION_BROKER_CAPACITY')
        for entry in self._generations.values():
            old = entry['registration'].identity
            if (old.owner.process_id == registration.identity.owner.process_id or old.owner.generation == registration.identity.owner.generation
                    or old.credential_resource == registration.identity.credential_resource or old.service_identity == registration.identity.service_identity
                    or entry['credentials'].api_key == credentials.api_key):
                raise StorageError('GENERATION_IDENTITY_REUSE_DENIED')
        first = self._current is None
        self._account_id = registration.process.account_id
        self._generations[registration.client_id] = dict(registration=registration, credentials=credentials,
                                                         mode='STAGED' if first else 'WAITING', kind='TRADING', seen=set(), manifest=None,
                                                         expires_at=registration.identity.owner.expires_at, grant_id=None, lease_epoch=None,
                                                         sources={(str(registration.identity.owner.process_id), str(registration.identity.owner.generation), str(registration.identity.owner.session_id), registration.identity.owner.epoch)})
        if first:
            self._current = registration.client_id

    def bind_head(self, registration, grant_id, lease_epoch):
        entry = self._entry(registration)
        if (self._current != registration.client_id or not isinstance(grant_id, UUID) or type(lease_epoch) is not int or lease_epoch <= 0
                or entry['grant_id'] is not None and (entry['grant_id'], entry['lease_epoch']) != (grant_id, lease_epoch)):
            raise StorageError('GENERATION_HEAD_INVALID')
        entry['grant_id'], entry['lease_epoch'] = grant_id, lease_epoch
        if entry['mode'] == 'STAGED':
            entry['mode'] = 'UNARMED'

    def enable(self, registration, *, mode, expires_at):
        entry = self._entry(registration)
        if (self._current != registration.client_id or mode not in ('ACTIVE', 'RECOVERY_ONLY') or entry['mode'] != 'UNARMED'
                or entry['kind'] == 'RECOVERY' and mode != 'RECOVERY_ONLY'
                or not datetime.now(timezone.utc) < utc(expires_at) <= registration.identity.owner.expires_at):
            raise StorageError('GENERATION_REARM_DENIED')
        entry['mode'], entry['expires_at'] = mode, expires_at
        if mode == 'RECOVERY_ONLY':
            entry['kind'] = 'RECOVERY'

    def pin_manifest(self, registration, manifest):
        from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import CancellationManifest
        entry = self._entry(registration)
        if (type(manifest) is not CancellationManifest or manifest.binding != self.binding
                or manifest.database_lineage != self.database_lineage or manifest.account_id != self._account_id
                or any((str(v.process_id), str(v.generation), str(v.session_id), v.epoch) not in entry['sources'] for v in manifest.identities)
                or self._current != registration.client_id
                or entry['mode'] not in ('UNARMED', 'ACTIVE', 'RECOVERY_ONLY')):
            raise StorageError('GENERATION_MANIFEST_SCOPE_INVALID')
        # The trusted DB exporter/control host owns this call; it is not an RPC.
        entry['manifest'] = manifest

    def bind_cancellation_source(self, registration, repository):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.cancellation_manifest import (
            PgCancellationManifestRepository,
        )
        entry = self._entry(registration)
        if (type(repository) is not PgCancellationManifestRepository or entry['kind'] != 'TRADING'
                or self._current != registration.client_id
                or repository.runtime.config.binding != self.binding
                or repository.runtime.config.database_lineage != self.database_lineage
                or repository.runtime.config.account_id != self._account_id):
            raise StorageError('GENERATION_CANCEL_SOURCE_INVALID')
        # Trusted control assembly only; neither repository nor owner is supplied
        # by the runtime RPC. Recovery actors retain explicitly pinned manifests.
        entry['cancellation_source'] = repository

    async def begin_exclusion(self, registration):
        entry = self._entry(registration)
        if self._current != registration.client_id or entry['mode'] not in ('ACTIVE', 'UNARMED', 'RECOVERY_ONLY') or self.provider is None:
            raise StorageError('GENERATION_PROVIDER_REQUIRED')
        entry['mode'] = 'DRAINING'  # Close all writes before the provider await.
        await self.provider.revoke(registration.identity.credential_resource)

    async def certify(self, source, target, *, expires_at):
        previous, replacement = self._entry(source), self._entry(target)
        if (self._current != source.client_id or previous['mode'] != 'DRAINING' or replacement['mode'] != 'WAITING'
                or previous['grant_id'] is None or self.provider is None):
            raise StorageError('GENERATION_EXCLUSION_NOT_READY')
        report = await self.provider.exclusion(source.identity.credential_resource)
        if self._current != source.client_id or previous['mode'] != 'DRAINING' or replacement['mode'] != 'WAITING':
            raise StorageError('GENERATION_EXCLUSION_CONTEXT_CHANGED')
        cert = CredentialExclusion('generation-provider', self.binding, self.database_lineage,
                                   previous['grant_id'], previous['lease_epoch'], source.identity, target.identity, target.process.capability_hash,
                                   report['requested_at'], report['effective_at'], report['observed_at'], expires_at, tuple(report['accepted_request_ids']),
                                   report['rest_denied'], report['ws_denied'], report['caa_disabled'], report['inflight_reconciled'], '0' * 128)
        verifier = CredentialExclusionVerifier('generation-provider', self.public_key)
        from dataclasses import replace
        cert = replace(cert, signature=self._signer.sign(encode(cert.body())).hex())
        verifier.verify(cert, binding=self.binding, database_lineage=self.database_lineage,
                        predecessor_grant=previous['grant_id'], predecessor_epoch=previous['lease_epoch'], predecessor_owner=source.identity.owner,
                        target_registration=target.process, now=datetime.now(timezone.utc))
        self._certified[target.client_id] = fingerprint(dict(body=cert.body(), signature=cert.signature))
        previous['mode'] = 'EXCLUDED'
        return cert

    def admit(self, target, certificate, *, recovery):
        entry = self._entry(target)
        if (type(recovery) is not bool or type(certificate) is not CredentialExclusion or entry['mode'] != 'WAITING'
                or self._certified.get(target.client_id) != fingerprint(dict(body=certificate.body(), signature=certificate.signature))
                or datetime.now(timezone.utc) >= certificate.expires_at
                or self._generations[self._current]['mode'] != 'EXCLUDED'):
            raise StorageError('GENERATION_EXCLUSION_NOT_READY')
        previous = self._generations[self._current]
        if recovery:
            entry['sources'] = set(previous['sources'])
        self._current, entry['mode'], entry['kind'] = target.client_id, 'RECOVERY_ONLY' if recovery else 'UNARMED', 'RECOVERY' if recovery else 'TRADING'
        entry['expires_at'] = certificate.target.owner.expires_at

    async def dispatch(self, call):
        if type(call) is not BrokerCall or call.action not in ('FETCH', 'AUTHORIZE', 'WS_READ', 'CANCEL_MANIFEST') or len(encode(call.body())) > 131072:
            raise StorageError('GENERATION_RPC_INVALID')
        entry = self._generations.get(call.client_id)
        if entry is None:
            raise StorageError('GENERATION_CLIENT_UNKNOWN')
        try:
            Ed25519PublicKey.from_public_bytes(entry['registration'].public_key).verify(call.signature, encode(call.body()))
        except (InvalidSignature, ValueError, TypeError):
            raise StorageError('GENERATION_RPC_SIGNATURE_INVALID') from None
        if type(call.sequence) is not int or not 1 <= call.sequence <= self.max_calls or call.sequence in entry['seen']:
            raise StorageError('GENERATION_RPC_REPLAY_OR_CAPACITY')
        entry['seen'].add(call.sequence)
        if (self._current != call.client_id or entry['mode'] not in ('UNARMED', 'ACTIVE', 'RECOVERY_ONLY')
                or entry['grant_id'] is None or datetime.now(timezone.utc) >= entry['expires_at']):
            raise StorageError('GENERATION_NOT_CURRENT')
        if call.action == 'FETCH':
            if call.payload != {'resource': entry['registration'].identity.credential_resource}:
                raise StorageError('GENERATION_SECRET_ACCESS_DENIED')
            result = dict(api_key=entry['credentials'].api_key, secret=entry['credentials'].secret, passphrase=entry['credentials'].passphrase, account_uid=self.binding.account_uid)
        elif call.action == 'CANCEL_MANIFEST':
            if (set(call.payload) != {'max_cancels'} or type(call.payload['max_cancels']) is not int
                    or not 0 < call.payload['max_cancels'] <= 64):
                raise StorageError('GENERATION_CANCEL_BOUND_INVALID')
            repository = entry.get('cancellation_source')
            if repository is not None:
                fresh = await repository.capture(source_owner=entry['registration'].identity.owner)
                self.pin_manifest(entry['registration'], fresh)
                if datetime.now(timezone.utc) >= entry['expires_at']:
                    raise StorageError('GENERATION_NOT_CURRENT')
            manifest = entry['manifest']
            if manifest is None:
                raise StorageError('GENERATION_CANCEL_MANIFEST_REQUIRED')
            if len(manifest.identities) > call.payload['max_cancels']:
                raise StorageError('GENERATION_CANCEL_BOUND_EXCEEDED')
            # This is only a signed identity read. Every subsequent exact
            # request still reauthorizes against the current broker manifest.
            result = dict(identities=[v.json() for v in manifest.identities])
        elif call.action == 'WS_READ':
            if set(call.payload) != {'op', 'channels', 'key_hash'} or call.payload['op'] not in ('login', 'subscribe', 'unsubscribe'):
                raise StorageError('GENERATION_WS_WRITE_DENIED')
            allowed = {'orders', 'account', 'positions', 'balance_and_position'}
            if (not set(call.payload['channels']) <= allowed or call.payload['key_hash'] != hashlib.sha256(entry['credentials'].api_key.encode()).hexdigest()):
                raise StorageError('GENERATION_WS_SCOPE_DENIED')
            result = dict(kind='READ', digest=fingerprint(call.payload))
        else:
            result = self._authorize(entry, call.payload)
        body = dict(client_id=call.client_id, sequence=call.sequence, action=call.action, result=result)
        return BrokerReply(body, self._signer.sign(encode(body)))

    def _authorize(self, entry, payload):
        if set(payload) != {'request', 'financial_guard'} or type(payload['financial_guard']) is not bool:
            raise StorageError('GENERATION_REQUEST_INVALID')
        request = payload['request']
        if type(request) is not dict or set(request) != {'method', 'url', 'params', 'data', 'authenticated', 'api_key_sha256'}:
            raise StorageError('GENERATION_REQUEST_INVALID')
        url = urlsplit(request['url'])
        if (url.scheme + '://' + url.netloc not in self.origins or url.query or url.fragment or url.username or url.password
                or not request['authenticated'] or request['api_key_sha256'] != hashlib.sha256(entry['credentials'].api_key.encode()).hexdigest()):
            raise StorageError('GENERATION_REQUEST_SCOPE_INVALID')
        if request['method'] == 'GET' and url.path in _READS and request['data'] is None:
            kind = 'READ'
        elif request['method'] == 'POST' and url.path == '/api/v5/trade/cancel-order' and not payload['financial_guard']:
            manifest = entry['manifest']
            if manifest is None or not manifest.matches(request):
                raise StorageError('GENERATION_EXACT_CANCEL_IDENTITY_REQUIRED')
            kind = 'EMERGENCY_CANCEL'
        elif (request['method'] == 'POST' and url.path in _NORMAL and entry['mode'] == 'ACTIVE' and payload['financial_guard']):
            kind = 'NORMAL_WRITE'
        else:
            raise StorageError('GENERATION_WRITE_DENIED')
        return dict(kind=kind, digest=fingerprint(request))


class GenerationClient:
    def __init__(self, identity, rpc, *, public_key, local_ready, timeout_ms):
        if (type(identity) is not BrokerIdentity or not callable(rpc) or type(public_key) is not bytes or len(public_key) != 32
                or not callable(local_ready) or type(timeout_ms) is not int or not 0 < timeout_ms <= 60000):
            raise StorageError('GENERATION_CLIENT_CONFIG_INVALID')
        self.identity, self.rpc, self.public_key, self.local_ready, self.timeout_ms = identity, rpc, public_key, local_ready, timeout_ms
        self._context = identity, rpc, public_key, local_ready, timeout_ms
        self._stopped, self._pid, self._key_hash = False, os.getpid(), None
        self._account_uid, self._uid_verified = None, False

    def _original(self):
        if (self._context != (self.identity, self.rpc, self.public_key, self.local_ready, self.timeout_ms)
                or self._pid != os.getpid() or self._stopped):
            raise StorageError('GENERATION_CLIENT_STOPPED')

    def stop(self):
        self._stopped = True

    async def _call(self, action, payload):
        self._original()
        call = self.identity.call(action, payload)
        try:
            async with asyncio.timeout(self.timeout_ms / 1000):
                reply = await self.rpc(call)
        except (TimeoutError, ConnectionError, EOFError, OSError):
            raise StorageUnavailable('GENERATION_BROKER_UNAVAILABLE') from None
        if type(reply) is not BrokerReply:
            raise StorageError('GENERATION_BROKER_REPLY_INVALID')
        try:
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(reply.signature, encode(reply.body))
        except (InvalidSignature, ValueError, TypeError):
            raise StorageError('GENERATION_BROKER_REPLY_INVALID') from None
        if (set(reply.body) != {'client_id', 'sequence', 'action', 'result'} or reply.body['client_id'] != call.client_id
                or reply.body['sequence'] != call.sequence or reply.body['action'] != call.action):
            raise StorageError('GENERATION_BROKER_REPLY_SCOPE_INVALID')
        self._original()
        return reply.body['result']

    async def credentials(self):
        result = await self._call('FETCH', dict(resource=self.identity.registration.identity.credential_resource))
        if type(result) is not dict or set(result) != {'api_key', 'secret', 'passphrase', 'account_uid'} or type(result['account_uid']) is not str or not result['account_uid']:
            raise StorageError('GENERATION_CREDENTIAL_REPLY_INVALID')
        self._account_uid = result['account_uid']
        credentials = GenerationCredentials(**{k: result[k] for k in ('api_key', 'secret', 'passphrase')})
        self._key_hash = hashlib.sha256(credentials.api_key.encode()).hexdigest()
        return credentials

    async def install(self, connectors):
        if type(connectors) is not dict or not connectors or set(connectors) - {'okx', 'okx_perpetual'}:
            raise StorageError('GENERATION_CONNECTORS_INVALID')
        credentials = await self.credentials()
        for name, connector in connectors.items():
            auth = connector._web_assistants_factory.auth
            key = getattr(auth, 'api_key', None) if name == 'okx' else getattr(auth, '_api_key', None)
            secret = getattr(auth, 'secret_key', None) if name == 'okx' else getattr(auth, '_api_secret', None)
            passphrase = getattr(auth, 'passphrase', None) if name == 'okx' else getattr(auth, '_passphrase', None)
            if (key, secret, passphrase) != (credentials.api_key, credentials.secret, credentials.passphrase):
                raise StorageError('GENERATION_SDK_CREDENTIAL_SCOPE_INVALID')
        try:
            for connector in connectors.values():
                connector._web_assistants_factory.bind_generation_access(self)
                response = await connector._api_get('/api/v5/account/config', is_auth_required=True)
                rows = response.get('data') if type(response) is dict else None
                if (type(response) is not dict or response.get('code') != '0' or type(rows) is not list or len(rows) != 1
                        or type(rows[0]) is not dict or rows[0].get('uid') != self._account_uid):
                    raise StorageError('GENERATION_AUTHENTICATED_UID_MISMATCH')
            self._uid_verified = True
        except BaseException:
            self.stop()
            raise

    async def cancellation_identities(self, *, max_cancels):
        from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import CancellationIdentity
        if type(max_cancels) is not int or not 0 < max_cancels <= 64:
            raise StorageError('GENERATION_CANCEL_BOUND_INVALID')
        result = await self._call('CANCEL_MANIFEST', dict(max_cancels=max_cancels))
        if (type(result) is not dict or set(result) != {'identities'} or type(result['identities']) is not list
                or len(result['identities']) > max_cancels):
            raise StorageError('GENERATION_CANCEL_REPLY_INVALID')
        try:
            identities = tuple(CancellationIdentity.parse(v) for v in result['identities'])
        except (TypeError, ValueError, KeyError):
            raise StorageError('GENERATION_CANCEL_REPLY_INVALID') from None
        if len({v.wire_id for v in identities}) != len(identities):
            raise StorageError('GENERATION_CANCEL_REPLY_INVALID')
        return identities

    async def cancel_exact(self, connector, identity):
        from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import CancellationIdentity
        if type(identity) is not CancellationIdentity:
            raise StorageError('GENERATION_CANCEL_IDENTITY_INVALID')
        if (not self._uid_verified or connector._web_assistants_factory._generation_access.current is not self
                or connector.name != ('okx' if identity.product == 'SPOT' else 'okx_perpetual')):
            raise StorageError('GENERATION_SDK_BINDING_REQUIRED')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
        quota = PgSharedSdkQuota.for_connector(connector)
        # Revoked local trading must not queue an exact cancellation behind
        # the original blocked HTTP collection. SDK/shared rate limits and the
        # broker's final exact-request guard still apply without modification.
        lane = quota.emergency_cancellation(self, connector) if quota is not None and self.local_ready() is False else nullcontext()
        async with lane:
            response = await connector._api_post('/api/v5/trade/cancel-order', data=dict(instId=identity.instrument,
                                                                                         clOrdId=identity.wire_id, ordId=identity.exchange_order_id), is_auth_required=True)
        rows = response.get('data') if type(response) is dict else None
        if (type(response) is not dict or response.get('code') != '0' or type(rows) is not list or len(rows) != 1
                or type(rows[0]) is not dict or rows[0].get('sCode') not in ('0', '51400', '51401')
                or rows[0].get('ordId') != identity.exchange_order_id or rows[0].get('clOrdId') != identity.wire_id):
            raise StorageError('GENERATION_CANCEL_RESPONSE_UNQUALIFIED')
        return True  # Cancel ACK never releases a native financial hold.

    def request_guard(self, *, financial_guard):
        self._original()
        return _GenerationGuard(self, financial_guard)

    def rest_guard(self, request, financial_guard):
        path = urlsplit(request.url).path
        if request.method == RESTMethod.GET and not request.is_auth_required and not path.startswith(('/api/v5/account/', '/api/v5/trade/', '/api/v5/users/', '/api/v5/asset/')):
            return financial_guard
        guard = self.request_guard(financial_guard=financial_guard is not None)
        if financial_guard is None:
            return guard
        from hummingbot.core.web_assistant.connections.request_guard import CompositeRESTRequestGuard
        return CompositeRESTRequestGuard((guard, financial_guard))

    async def authorize_ws(self, payload):
        self._original()
        if payload == 'ping':
            return True
        if type(payload) is not dict or payload.get('op') not in ('login', 'subscribe', 'unsubscribe'):
            raise StorageError('GENERATION_WS_WRITE_DENIED')
        args = payload.get('args')
        if type(args) is not list or not args or len(args) > 32 or any(type(a) is not dict for a in args):
            raise StorageError('GENERATION_WS_SCOPE_DENIED')
        if payload['op'] == 'login':
            if len(args) != 1 or hashlib.sha256(args[0].get('apiKey', '').encode()).hexdigest() != self._key_hash:
                raise StorageError('GENERATION_WS_SCOPE_DENIED')
            channels = []
        else:
            channels = [a.get('channel') for a in args]
            public = {'books', 'books5', 'books50-l2-tbt', 'books-l2-tbt', 'trades', 'tickers', 'instruments', 'mark-price', 'index-tickers', 'funding-rate'}
            if all(v in public for v in channels):
                return True
        await self._call('WS_READ', dict(op=payload['op'], channels=channels, key_hash=self._key_hash))
        return True

    def __reduce__(self):
        raise StorageError('GENERATION_CLIENT_SERIALIZATION_DENIED')


class _GenerationGuard:
    def __init__(self, client, financial_guard):
        if type(financial_guard) is not bool:
            raise StorageError('GENERATION_REQUEST_INVALID')
        self.client, self.financial_guard = client, financial_guard
        self._request = self._task = self._kind = None
        self._consumed = False

    async def authorize(self, request):
        if self._task is not None:
            return False
        self._task = asyncio.current_task()
        started = time.monotonic()
        body = request_body(request)
        result = await self.client._call('AUTHORIZE', dict(request=body, financial_guard=self.financial_guard))
        if result != dict(kind=result.get('kind'), digest=fingerprint(body)) or result['kind'] not in ('READ', 'EMERGENCY_CANCEL', 'NORMAL_WRITE'):
            raise StorageError('GENERATION_PERMIT_INVALID')
        self._request, self._kind, self._deadline = request, result['kind'], started + self.client.timeout_ms / 1000
        return True

    def check(self, request):
        try:
            self.client._original()
            if (self._consumed or self._task is not asyncio.current_task() or request != self._request
                    or time.monotonic() >= self._deadline or self._kind == 'NORMAL_WRITE' and self.client.local_ready() is not True):
                return False
            self._consumed = True
            return True
        except Exception:
            return False

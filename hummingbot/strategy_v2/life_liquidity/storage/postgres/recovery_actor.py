"""Explicit replacement observation actor; never a trading activation.

The signed native logical owner/session stays intact for original financial
provenance. A new private process lease delegates only that owner's late facts.
Every allocating SQL guard still denies RECOVERY_ONLY, even for direct callers.
"""
import asyncio
import inspect
import json
import os
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import (
    CredentialExclusion,
    CredentialExclusionVerifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import (
    PgProcessLease,
    ProcessCapability,
    ProcessRegistration,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


class PgRecoveryGrantIssuer:
    def __init__(self, control, *, credential_verifier):
        if type(control) is not PgControlRepository or type(credential_verifier) is not CredentialExclusionVerifier:
            raise StorageError('PG_RECOVERY_ISSUER_INVALID')
        self.control, self.credential_verifier = control, credential_verifier
        self._context = control, credential_verifier

    @staticmethod
    def request(operation_id, grant_id, registration, certificate, source_owner):
        identifier(operation_id)
        if (not isinstance(grant_id, UUID) or not grant_id.int or type(registration) is not ProcessRegistration
                or type(certificate) is not CredentialExclusion or type(source_owner) is not OwnerToken):
            raise StorageError('PG_RECOVERY_REQUEST_INVALID')
        return dict(kind='PROCESS_RECOVERY_V1', operation_id=operation_id, grant_id=str(grant_id),
                    mode='RECOVERY_ONLY', source_owner=source_owner.json(), owner=registration.owner.json(),
                    account_id=str(registration.account_id), database_lineage=str(registration.database_lineage),
                    principal=registration.principal, capability_hash=registration.capability_hash,
                    exclusion_digest=fingerprint(dict(body=certificate.body(), signature=certificate.signature)))

    async def register(self, operation_id, grant_id, registration, *, source_owner, certificate, approval, facts, cut):
        control, verifier = self.control, self.credential_verifier
        if self._context != (control, verifier):
            raise StorageError('PG_RECOVERY_ISSUER_CONTEXT_CHANGED')
        payload = self.request(operation_id, grant_id, registration, certificate, source_owner)
        if (type(approval) is not OperatorApproval or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut
                or facts.binding != control.binding):
            raise StorageError('PG_RECOVERY_REQUEST_INVALID')
        if control._mutex.locked() or control._tasks:
            raise StorageUnavailable('PG_RECOVERY_ISSUER_BACKPRESSURE')
        cfg = control.runtime.config
        if (registration.account_id != cfg.account_id or registration.database_lineage != cfg.database_lineage
                or registration.principal != cfg.principal or source_owner.session_id != registration.owner.session_id
                or source_owner.epoch != registration.owner.epoch):
            raise StorageError('PG_RECOVERY_REGISTRATION_INVALID')
        async with control._mutex, asyncio.timeout(cfg.transaction_timeout_ms / 1000):
            control._original()
            await control._primary()
            c = control.connection
            async with c.transaction():
                for name, value in (('statement_timeout', cfg.statement_timeout_ms), ('lock_timeout', cfg.acquire_timeout_ms),
                                    ('idle_in_transaction_session_timeout', cfg.idle_transaction_timeout_ms)):
                    await c.execute('SELECT set_config($1,$2,true)', name, str(value))
                account = await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
                config = json.loads(await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2',
                                                     cfg.account_id, cfg.binding.config_hash))
                policy, _, _ = await control._history()
                lease_policy = await c.fetchrow('SELECT * FROM life_financial.process_lease_policies WHERE account_id=$1', cfg.account_id)
                if (account['environment'] != 'OFFLINE_SIMULATION' or account['mode'] != 'RECOVERY_ONLY'
                        or OwnerToken(UUID(str(account['owner_process'])), UUID(str(account['owner_generation'])), account['owner_epoch'], UUID(str(account['current_session'])), account['owner_since'], account['owner_until']) != source_owner or config.get('credential_authority') != verifier.policy()
                        or config.get('operator_verifier') != control.verifier.policy() or lease_policy is None
                        or lease_policy['policy_hash'] != cfg.binding.policy_hash
                        or config.get('process_lease') != dict(lease_duration_ms=lease_policy['lease_duration_ms'], max_registrations=lease_policy['max_registrations'])):
                    raise StorageError('PG_RECOVERY_POLICY_OR_NATIVE_MODE_INVALID')
                head = await c.fetchrow('SELECT * FROM life_financial.process_leases WHERE account_id=$1', cfg.account_id)
                if head is None:
                    raise StorageError('PG_RECOVERY_PREDECESSOR_MISSING')
                previous = await c.fetchval('SELECT owner FROM life_financial.process_lease_registrations WHERE account_id=$1 AND grant_id=$2', cfg.account_id, head['grant_id'])
                identity = await c.fetchval('SELECT life_financial.process_generation_identity($1,$2)', cfg.account_id, head['grant_id'])
                if head['lease_epoch'] > 1 and (identity is None or json.loads(identity) != certificate.source.json()):
                    raise StorageError('PG_RECOVERY_PREDECESSOR_IDENTITY_CHANGED')
                now = await c.fetchval('SELECT clock_timestamp()')
                verifier.verify(certificate, binding=cfg.binding, database_lineage=cfg.database_lineage,
                                predecessor_grant=head['grant_id'], predecessor_epoch=head['lease_epoch'],
                                predecessor_owner=OwnerToken.from_json(json.loads(previous)), target_registration=registration, now=now)
                version, digest = control.financial._cut(cut, source_owner)
                state = json.loads(await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
                if (version != account['version'] or digest != state['digest'] or facts.observed_at < certificate.observed_at
                        or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= policy['max_fact_age_ms']
                        or (registration.owner.expires_at - now).total_seconds() * 1000 > policy['successor_duration_ms']):
                    raise StorageError('PG_RECOVERY_CUT_OR_TIME_INVALID')
                control.verifier.verify(approval, cfg.binding, 'HANDOFF', payload, cut, facts, now)
                native = await c.fetchrow('SELECT operation_id,body FROM life_financial.control_records WHERE account_id=$1 ORDER BY sequence DESC LIMIT 1', cfg.account_id)
                if native is None or json.loads(native['body'])['payload'].get('mode') != 'RECOVERY_ONLY':
                    raise StorageError('PG_RECOVERY_NATIVE_RECOVER_REQUIRED')
                record = dict(payload=payload, approval=approval.body(), signature=approval.signature, cut=cut.json(), facts=facts.json(),
                              operator_policy=control.verifier.policy(), credential_policy=verifier.policy(),
                              exclusion=dict(body=certificate.body(), signature=certificate.signature),
                              source_owner=source_owner.json(), control_operation=native['operation_id'])
                body = json.dumps(record)
                if len(body.encode()) > 131072:
                    raise StorageError('PG_RECOVERY_RECORD_TOO_LARGE')
                prior = await c.fetchval('SELECT body FROM life_financial.process_recovery_grants WHERE account_id=$1 AND operation_id=$2', cfg.account_id, operation_id)
                if prior is not None:
                    if json.loads(prior) != record:
                        raise StorageError('PG_RECOVERY_OPERATION_CONFLICT')
                    return  # Registration readback never creates a ready actor.
                count = await c.fetchval('SELECT count(*) FROM life_financial.process_lease_registrations WHERE account_id=$1', cfg.account_id)
                if count >= lease_policy['max_registrations']:
                    raise StorageError('PG_RECOVERY_REGISTRATION_BOUND')
                owner = registration.owner
                await c.execute('''INSERT INTO life_financial.activation_grants VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)''',
                                cfg.account_id, grant_id, owner.process_id, owner.generation, owner.epoch, owner.session_id,
                                owner.valid_from, owner.expires_at, bytes.fromhex(approval.signature))
                await c.execute('INSERT INTO life_financial.grant_consumptions VALUES($1,$2,$3)', cfg.account_id, grant_id, operation_id)
                await c.execute('''INSERT INTO life_financial.process_recovery_grants
                    VALUES($1,$2,$3,$4,$5,$6::jsonb,encode(sha256(convert_to(($6::jsonb)::text,'UTF8')),'hex'),$7)''',
                                cfg.account_id, operation_id, grant_id, head['grant_id'], head['lease_epoch'], body, now)
                await c.execute('INSERT INTO life_financial.process_lease_registrations VALUES($1,$2,$3,$4::jsonb,$5,$6,$7)',
                                cfg.account_id, grant_id, registration.principal, json.dumps(owner.json()), registration.capability_hash, now, native['operation_id'])


class PgRecoveryActor:
    @classmethod
    async def open(cls, config, *, owner, claim_operation_id, heartbeat_interval_ms, register):
        identifier(claim_operation_id)
        if (type(owner) is not OwnerToken or not callable(register) or type(heartbeat_interval_ms) is not int or heartbeat_interval_ms <= 0):
            raise StorageError('PG_RECOVERY_ACTOR_CONFIG_INVALID')
        actor = cls()
        actor.runtime = await PgRuntime.open(config)
        actor.financial = PgSpotRepository(actor.runtime)
        actor.lease = PgProcessLease(actor.runtime, ProcessCapability.create(config, owner))
        actor._stopped, actor._task, actor._sequence = False, None, 0
        actor._claim = claim_operation_id
        actor._interval = heartbeat_interval_ms / 1000
        try:
            async with asyncio.timeout(config.transaction_timeout_ms / 1000):
                policy = await actor.runtime._admit(lambda c: c.fetchval('SELECT lease_duration_ms FROM life_financial.process_lease_policies WHERE account_id=$1', config.account_id), safety=True)
                if policy is None or heartbeat_interval_ms * 2 >= policy:
                    raise StorageError('PG_RECOVERY_HEARTBEAT_POLICY_INVALID')
                registration = register(actor.lease.capability.registration)
                if not inspect.isawaitable(registration):
                    raise StorageError('PG_RECOVERY_ASYNC_REGISTRATION_REQUIRED')
                await registration
            receipt = await actor.lease.claim(claim_operation_id)
            if receipt.replayed or not actor.lease.ready:
                raise StorageError('PG_RECOVERY_ORIGINAL_CLAIM_REQUIRED')
            row = await actor.runtime._admit(lambda c: c.fetchval("SELECT body->'source_owner' FROM life_financial.process_recovery_grants WHERE account_id=$1 AND grant_id=$2", config.account_id, receipt.grant_id), safety=True)
            if row is None:
                raise StorageError('PG_RECOVERY_DELEGATION_REQUIRED')
            actor.source_owner = OwnerToken.from_json(row)
            actor._context = (actor.runtime, actor.financial, actor.lease, actor.source_owner, actor._claim, actor._interval, os.getpid())
            actor._task = asyncio.create_task(actor._heartbeat())
            return actor
        except BaseException:
            await actor.close()
            raise

    @property
    def ready(self):
        original = getattr(self, '_context', None) == (self.runtime, self.financial, self.lease, getattr(self, 'source_owner', None), self._claim, self._interval, os.getpid())
        if not original:
            self._stopped = True
            original_runtime = getattr(self, '_context', (self.runtime,))[0]
            original_runtime._lease_ready = False
        return original and not self._stopped and self.lease.ready

    @property
    def mode(self):
        return 'RECOVERY_ONLY' if self.ready else 'STOPPED'

    async def _heartbeat(self):
        try:
            while self.ready:
                await asyncio.sleep(self._interval)
                self._sequence += 1
                operation = 'recovery-beat:' + fingerprint(dict(claim=self._claim, sequence=self._sequence))
                receipt = await self.lease.heartbeat(operation)
                if receipt.replayed:
                    raise StorageError('PG_RECOVERY_ORIGINAL_HEARTBEAT_REQUIRED')
        except BaseException:
            self._stopped = True
            self.lease.stop()

    async def apply_received(self, command):
        if not self.ready:
            raise StorageError('PG_RECOVERY_ACTOR_STOPPED')
        return await self.financial.apply_received(command, owner=self.source_owner)

    async def close(self):
        self._stopped = True
        original = getattr(self, '_context', (self.runtime, self.financial, self.lease))
        original[2].stop()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await original[1].close()

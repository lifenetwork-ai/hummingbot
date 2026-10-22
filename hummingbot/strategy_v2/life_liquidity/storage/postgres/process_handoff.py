"""Explicit, signed clean cold handoff of an offline native process lease.

This does not implement a replacement recovery actor for an unresolved old
financial cut. A separately pinned credential authority and a HANDOFF operator
must authorize one exact target after external exclusion and native quiescence.
"""
import asyncio
import json
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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgLeaseGrantIssuer, ProcessRegistration
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


class PgProcessHandoffIssuer:
    def __init__(self, control, *, credential_verifier):
        if type(control) is not PgControlRepository or type(credential_verifier) is not CredentialExclusionVerifier:
            raise StorageError('PG_PROCESS_HANDOFF_ISSUER_INVALID')
        self.control, self.credential_verifier = control, credential_verifier
        self._context = control, credential_verifier

    @staticmethod
    def request(operation_id, grant_id, registration, certificate):
        identifier(operation_id)
        if (not isinstance(grant_id, UUID) or not grant_id.int or type(registration) is not ProcessRegistration
                or type(certificate) is not CredentialExclusion):
            raise StorageError('PG_PROCESS_HANDOFF_REQUEST_INVALID')
        return dict(kind='PROCESS_HANDOFF_V1', operation_id=operation_id, target_grant=str(grant_id),
                    account_id=str(registration.account_id), database_lineage=str(registration.database_lineage),
                    principal=registration.principal, owner=registration.owner.json(), capability_hash=registration.capability_hash,
                    exclusion_digest=fingerprint(dict(body=certificate.body(), signature=certificate.signature)))

    async def register_successor(self, operation_id, grant_id, registration, *, certificate, approval, facts, cut):
        control, verifier = self.control, self.credential_verifier
        if self._context != (control, verifier):
            raise StorageError('PG_PROCESS_HANDOFF_CONTEXT_CHANGED')
        request = self.request(operation_id, grant_id, registration, certificate)
        if (type(approval) is not OperatorApproval or type(facts) is not SpotFinancialRecoveryFacts
                or type(cut) is not FinancialCut or facts.binding != control.binding):
            raise StorageError('PG_PROCESS_HANDOFF_REQUEST_INVALID')
        if control._mutex.locked() or control._tasks:
            raise StorageUnavailable('PG_PROCESS_HANDOFF_BACKPRESSURE')

        async def qualify(c, account):
            cfg = control.runtime.config
            configuration = json.loads(await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2',
                                                        cfg.account_id, cfg.binding.config_hash))
            if configuration.get('credential_authority') != verifier.policy():
                raise StorageError('PG_PROCESS_HANDOFF_CREDENTIAL_POLICY_INVALID')
            policy, _, _ = await control._history()
            head = await c.fetchrow('SELECT * FROM life_financial.process_leases WHERE account_id=$1', cfg.account_id)
            predecessor = await c.fetchval('SELECT owner FROM life_financial.process_lease_registrations WHERE account_id=$1 AND grant_id=$2',
                                           cfg.account_id, head['grant_id'])
            if head['lease_epoch'] > 1:
                identity = await c.fetchval("SELECT life_financial.process_generation_identity($1,$2)",
                                            cfg.account_id, head['grant_id'])
                if identity is None or json.loads(identity) != certificate.source.json():
                    raise StorageError('PG_PROCESS_HANDOFF_SOURCE_IDENTITY_CHANGED')
            now = await c.fetchval('SELECT clock_timestamp()')
            verifier.verify(certificate, binding=control.binding, database_lineage=cfg.database_lineage,
                            predecessor_grant=head['grant_id'], predecessor_epoch=head['lease_epoch'],
                            predecessor_owner=OwnerToken.from_json(json.loads(predecessor)), target_registration=registration, now=now)
            version, state_digest = control.financial._cut(cut, registration.owner)
            state = json.loads(await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
            if (version != account['version'] or state_digest != state['digest']
                    or facts.observed_at < certificate.observed_at
                    or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= policy['max_fact_age_ms']
                    or not await c.fetchval('SELECT life_financial.control_facts_qualified($1,$2::jsonb)', cfg.account_id, json.dumps(facts.json()))):
                raise StorageError('PG_PROCESS_HANDOFF_CUT_OR_FACTS_UNQUALIFIED')
            control.verifier.verify(approval, control.binding, 'HANDOFF', request, cut, facts, now)
            record = dict(payload=request, approval=approval.body(), signature=approval.signature, cut=cut.json(), facts=facts.json(),
                          operator_policy=control.verifier.policy(), credential_policy=verifier.policy(),
                          exclusion=dict(body=certificate.body(), signature=certificate.signature))
            body = json.dumps(record)
            if len(body.encode('utf-8')) > 131072:
                raise StorageError('PG_PROCESS_HANDOFF_RECORD_TOO_LARGE')
            old = await c.fetchval('SELECT body FROM life_financial.process_handoffs WHERE account_id=$1 AND operation_id=$2', cfg.account_id, operation_id)
            if old is not None:
                if json.loads(old) != record:
                    raise StorageError('PG_PROCESS_HANDOFF_OPERATION_CONFLICT')
                return  # Historical registration only; no process admission.
            await c.execute('''INSERT INTO life_financial.process_handoffs
                VALUES($1,$2,$3,$4,$5,$6::jsonb,encode(sha256(convert_to(($6::jsonb)::text,'UTF8')),'hex'),$7,$8)''',
                            cfg.account_id, operation_id, grant_id, head['grant_id'], head['lease_epoch'],
                            body, certificate.expires_at, now)

        async with control._mutex, asyncio.timeout(control.runtime.config.transaction_timeout_ms / 1000):
            # _register owns account serialization, original signed native
            # ACTIVATE verification and registration-at-COMMIT validation.
            await PgLeaseGrantIssuer(control)._register(grant_id, registration, handoff=qualify)

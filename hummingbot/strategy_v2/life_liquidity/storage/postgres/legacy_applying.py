"""Explicit retired File financial import; original basis is a prerequisite.

IMPORT retains a money-neutral JOINT_BATCH pending. A different signed RECOVER
command must run the existing native accounting kernels. Unsupported/missing
history denies. This cannot bootstrap a wallet, translate missing derivative
quantities or grant readiness. Expired unpublished approvals need a separately
signed successor; no retained approval or deadline is rewritten.
"""
import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval, OperatorVerifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import LegacyApplyingSource, RetireLegacyApplying
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class ImportLegacyApplying:
    source_digest: str
    batch: ApplyJointBatch
    supersedes_operation_id: str | None = None

    def __post_init__(self):
        digest(self.source_digest)
        if type(self.batch) is not ApplyJointBatch:
            raise StorageError('PG_LEGACY_APPLYING_COMMAND_INVALID')
        # Carry DTO arithmetic may retain equivalent trailing zero spellings.
        # Canonicalize at command construction, before signature/identity, so
        # cold decoding cannot change the approved request bytes afterwards.
        object.__setattr__(self, 'batch', ApplyJointBatch.from_json(self.batch.json()))
        if self.supersedes_operation_id is not None:
            identifier(self.supersedes_operation_id)
            if len(self.supersedes_operation_id) > 96 or self.supersedes_operation_id == self.operation_id:
                raise StorageError('PG_LEGACY_APPLYING_SUCCESSOR_INVALID')

    @property
    def operation_id(self):
        return self.batch.operation_id

    def json(self):
        result = dict(kind='IMPORT_LEGACY_APPLYING', operation_id=self.operation_id, source_digest=self.source_digest, batch=self.batch.json())
        if self.supersedes_operation_id is not None:
            result['supersedes_operation_id'] = self.supersedes_operation_id
        return result

    @classmethod
    def from_json(cls, value):
        try:
            if type(value) is not dict or set(value) != {'kind', 'operation_id', 'source_digest', 'batch'} | (
                    {'supersedes_operation_id'} if 'supersedes_operation_id' in value else set()):
                raise ValueError()
            command = cls(value['source_digest'], ApplyJointBatch.from_json(value['batch']), value.get('supersedes_operation_id'))
            if command.json() != value:
                raise ValueError()
            return command
        except (KeyError, ValueError, TypeError):
            raise StorageError('PG_LEGACY_APPLYING_COMMAND_INVALID') from None


def approval_from_record(record, binding):
    return OperatorApproval(**{**record['approval'], 'binding': binding, 'signature': record['signature'],
                               'issued_at': datetime.fromisoformat(record['approval']['issued_at']), 'expires_at': datetime.fromisoformat(record['approval']['expires_at'])})


def funding_profile(source, command):
    """All historical money must be present; never infer it from partial cash."""
    envelope = json.loads(source.checkpoint_bytes)
    resources = envelope['state']['resources']
    if (source.ancillary_images or len(source.core_pending_operation_ids) != 1
            or any(resources.get(k) for k in ('recovery_batches',
                                              'nav_operations', 'product_operations', 'lifecycle_operations'))
            or command.batch.swap_fills):
        raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    row = source.original_operations[source.core_pending_operation_ids[0]]
    old = JointCashSpot.from_json(row['command'])
    if old.spot_acks or old.spot_prepared_receipts or old.spot_terminal_operations or old.spot_settlement_operations:
        raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    original = {}
    for event in old.events:
        _, at, kind, bill = event.event_id.split(':')
        if kind != 'FUNDING' or bill in original:
            raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
        original[bill] = (int(at), event.signed_amount_usdt)
    payments = command.batch.funding.bundle.funding.payments
    if (not original or len(payments) != len(original)
            or {p.bill_id: (p.settled_at_ms, p.signed_payment_quote) for p in payments} != original):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    policy = envelope['policy']['legacy_spot']['accounting_policy']
    flows = {v.bill_id: v for v in old.spot_cashflows}
    retained = set()
    # A completed transfer remains account history even when the interrupted
    # command does not repeat it. Require its actual original APPLIED receipt;
    # a pending standalone transfer is not a completed prefix.
    for bill, raw in resources.get('cashflows', {}).items():
        flow = LegacySpotStore._cashflow_from_json(raw)
        if bill != flow.bill_id or bill in flows and flow != flows[bill]:
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        if bill not in flows:
            receipt = resources.get('cashflow_operations', {}).get(flow.operation_id)
            if (receipt is None or receipt['state'] != 'APPLIED'
                    or LegacySpotStore._cashflow_from_json(receipt['command']) != flow):
                raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
            retained.add(bill)
            flows[bill] = flow
    approved = {v['bill_id']: v for v in policy['approved_cashflows']}
    if (len(approved) != len(policy['approved_cashflows'])
            or len(command.batch.cashflows) != len(flows)
            or {v.bill_id for v in command.batch.cashflows} != set(flows)
            or set(source.facts.account_cashflow_ids) != set(flows)):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    expected_base, expected_cash = Decimal(policy['opening_base_life']), Decimal(policy['opening_quote_usdt'])
    for value in command.batch.cashflows:
        original_flow, approval = flows[value.bill_id], approved.get(value.bill_id)
        if (approval is None or any(value.json()[k] != original_flow.json()[k] for k in original_flow.json() if k != 'operation_id')
                or (approval['currency'], Decimal(approval['signed_amount']), approval['transfer_at_ms'])
                != (value.currency, value.signed_amount, value.transfer_at_ms)):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        expected_base += value.signed_amount if value.currency == 'LIFE' else Decimal('0')
        expected_cash += value.signed_amount if value.currency == 'USDT' else Decimal('0')
    for value in resources.get('cashflow_operations', {}).values():
        flow = LegacySpotStore._cashflow_from_json(value['command'])
        if flow.bill_id not in flows or flow != flows[flow.bill_id] or value['state'] not in ('CASHFLOWING', 'APPLIED'):
            raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
    if any(key not in flows or value != flows[key].json() for key, value in resources.get('cashflows', {}).items()):
        raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_funding_history import funding_history
    completed = funding_history(source, old, command.batch, flows, policy)
    before = json.loads(bytes.fromhex(row['before_native']['reservations']))
    if not retained <= {key.removeprefix('CASHFLOW:') for key in before['account_events'] if key.startswith('CASHFLOW:')}:
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    loss = json.loads(bytes.fromhex(row['before_native']['loss']))
    subsidy = json.loads(bytes.fromhex(row['before_native']['subsidy']))
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_spot_basis import spot_basis
    spot, spot_base, spot_cash, prefix_base, prefix_cash = spot_basis(source, old, command.batch, resources, before, loss, subsidy)
    before_base, before_cash = Decimal(policy['opening_base_life']) + prefix_base, Decimal(policy['opening_quote_usdt']) + prefix_cash
    for key, event in before['account_events'].items():
        if key.startswith('FEE:'):  # Exact prefix membership/value was checked by spot_basis.
            continue
        if key in completed:
            if (event[0], event[1], Decimal(event[2])) != ('JOINT', 'USDT', completed[key].signed_amount_usdt):
                raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
            before_cash += completed[key].signed_amount_usdt
            continue
        flow = flows.get(key.removeprefix('CASHFLOW:'))
        if (not key.startswith('CASHFLOW:') or flow is None
                or (event[0], event[1], Decimal(event[2])) != ('CASHFLOW', flow.currency, flow.signed_amount)):
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        before_base += flow.signed_amount if flow.currency == 'LIFE' else Decimal('0')
        before_cash += flow.signed_amount if flow.currency == 'USDT' else Decimal('0')
    if {key for key in before['account_events'] if key.startswith('JOINT:')} != set(completed):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    if ((source.facts.account_base_life, source.facts.account_quote_usdt) != (old.account_base_life, old.account_quote_usdt)
            or Decimal(before['life_balance']) != before_base or Decimal(before['usdt_balance']) != before_cash
            or any(int(at) < int(datetime.fromisoformat(policy['opened_at']).timestamp() * 1000) for at, _ in original.values())
            or old.account_base_life != expected_base + spot_base
            or old.account_quote_usdt != expected_cash + spot_cash + sum((amount for _, amount in original.values()), Decimal(0))):
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
    return {**policy, **spot}


class PgLegacyApplyingImporter:
    def __init__(self, connection, batch, *, verifier, source_verifier, max_fact_age_ms):
        if type(source_verifier) is not OperatorVerifier:
            raise StorageError('PG_LEGACY_APPLYING_SOURCE_VERIFIER_REQUIRED')
        # Reuse only the existing privileged connection/context checks; this is
        # a separate IMPORT role, table, policy and transaction, never RECOVER.
        self.context = PgJointRepairApprovalImporter(connection, batch, verifier=verifier, max_fact_age_ms=max_fact_age_ms)
        self.connection, self.batch, self.verifier, self.source_verifier = connection, batch, verifier, source_verifier
        self.financial, self.runtime, self.binding, self.owner = batch.financial, batch.runtime, batch.binding, batch.owner
        self.max_fact_age_ms, self._mutex = max_fact_age_ms, asyncio.Lock()

    def _original(self):
        self.context._original()
        if (self.connection is not self.context.connection or self.batch is not self.context.batch
                or self.verifier is not self.context.verifier or self.runtime is not self.context.runtime
                or self.financial is not self.context.financial or self.binding != self.context.binding or self.owner != self.context.owner):
            raise StorageError('PG_LEGACY_APPLYING_CONTEXT_CHANGED')

    def _audit(self, record, captured_at):
        try:
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                return self._audit_record(record, captured_at)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            raise StorageError('PG_LEGACY_APPLYING_PROOF_INVALID') from None

    def _audit_record(self, record, captured_at):
        source = LegacyApplyingSource.unpack(base64.b64decode(record['source'], validate=True))
        payload = record['payload']
        command = ImportLegacyApplying.from_json(payload)
        if command.json() != payload or source.content_digest != command.source_digest:
            raise StorageError('PG_LEGACY_APPLYING_SOURCE_CHANGED')
        retirement = record['retirement']
        retired = RetireLegacyApplying.from_json(retirement['payload'])
        retired.validate_source(source.cut.checkpoint.binding)
        if (retired.target_binding != self.binding or retired.target_database_lineage != self.runtime.config.database_lineage
                or retired.source_digest != source.content_digest or retirement['cut'] != source.cut.json()
                or retirement['facts'] != source.facts.json()
                or record['verifier_policy'] != self.verifier.policy() or record['source_verifier_policy'] != self.source_verifier.policy()):
            raise StorageError('PG_LEGACY_APPLYING_SCOPE_INVALID')
        self.source_verifier.audit(retirement, source.cut.checkpoint.binding)
        self.source_verifier.verify(approval_from_record(retirement, source.cut.checkpoint.binding), source.cut.checkpoint.binding,
                                    'IMPORT', retired.json(), source.cut, source.facts, datetime.fromisoformat(record['retired_at']))
        self.verifier.verify(approval_from_record(record, self.binding), self.binding, 'IMPORT', payload,
                             FinancialCut.from_json(record['cut']), SpotFinancialRecoveryFacts.from_json(record['facts']), captured_at)
        if source.ancillary_images:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
            policy = joint_profile(source, command)
        else:
            policy = funding_profile(source, command)
        # Existing imports before the explicit ACK basis retained the complete
        # signed File capsule without this redundant projection. Still verify
        # its actual ACK receipts above, preserving those original body bytes.
        if 'source_basis' in record and 'spot_acknowledgements' not in record['source_basis']:
            policy.pop('spot_acknowledgements', None)
        if 'source_basis' in record and record['source_basis'] != policy:
            raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
        facts = SpotFinancialRecoveryFacts.from_json(record['facts'])
        if (facts.account_base_life != source.facts.account_base_life or facts.account_quote_usdt != source.facts.account_quote_usdt
                or facts.liability_base_life or facts.liability_quote_usdt or not facts.order_scope_complete
                or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.account_trade_ids != source.facts.account_trade_ids
                or facts.account_cashflow_ids != source.facts.account_cashflow_ids):
            raise StorageError('PG_LEGACY_APPLYING_FACTS_CHANGED')
        return source, policy

    async def _row(self, operation_id):
        c, account = self.connection, self.runtime.config.account_id
        if not await c.fetchval('SELECT life_financial.legacy_applying_verified($1)', account):
            raise StorageUnavailable('PG_LEGACY_APPLYING_UNVERIFIED')
        row = await c.fetchrow('''SELECT CASE WHEN octet_length(body::text)<=4194304 THEN body END AS body,captured_at
            FROM life_financial.legacy_applying_imports WHERE account_id=$1 AND operation_id=$2''', account, operation_id)
        if row is None:
            return None
        if row['body'] is None:
            raise StorageUnavailable('PG_LEGACY_APPLYING_BOUND')
        record = json.loads(row['body'])
        self._audit(record, row['captured_at'])
        return record

    async def lookup(self, operation_id):
        self._original()
        identifier(operation_id)
        if self._mutex.locked():
            raise StorageUnavailable('PG_LEGACY_APPLYING_BACKPRESSURE')
        async with self._mutex, asyncio.timeout(self.runtime.config.transaction_timeout_ms / 1000):
            await self.context._owner()
            row = await self._row(operation_id)
            return None if row is None else await self.batch.lookup(operation_id, self.binding)

    async def _commit(self, transaction):
        await transaction.commit()

    async def _approve(self, command, source_store, approval, cut, facts):
        c, cfg = self.connection, self.runtime.config
        self._original()
        if (type(command) is not ImportLegacyApplying or type(approval) is not OperatorApproval
                or type(cut) is not FinancialCut or type(facts) is not SpotFinancialRecoveryFacts
                or facts.binding != self.binding):
            raise StorageError('PG_LEGACY_APPLYING_COMMAND_INVALID')
        self.financial._cut(cut, self.owner)
        if self._mutex.locked():
            raise StorageUnavailable('PG_LEGACY_APPLYING_BACKPRESSURE')
        async with self._mutex:
            async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                await self.context._owner()
            tx, committing = c.transaction(), False
            try:
                async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                    await tx.start()
                    for name, value in (('statement_timeout', cfg.statement_timeout_ms), ('lock_timeout', cfg.statement_timeout_ms),
                                        ('idle_in_transaction_session_timeout', cfg.idle_transaction_timeout_ms)):
                        await c.execute('SELECT set_config($1,$2,true)', name, str(value))
                    account = await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', cfg.account_id)
                    expected = dict(payload=command.json(), approval=approval.body(), signature=approval.signature,
                                    cut=cut.json(), facts=facts.json(), verifier_policy=self.verifier.policy(), source_verifier_policy=self.source_verifier.policy())
                    row = await self._row(command.operation_id)
                    if row is not None:
                        if any(row[k] != v for k, v in expected.items()):
                            raise StorageConflict('PG_LEGACY_APPLYING_IDENTITY_CONFLICT')
                        await tx.rollback()
                        return
                    parent = None
                    if command.supersedes_operation_id is not None:
                        parent = await self._row(command.supersedes_operation_id)
                        if (parent is None or parent['payload']['source_digest'] != command.source_digest
                                or await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2)', cfg.account_id, command.supersedes_operation_id)
                                or await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1 AND supersedes_operation_id=$2)', cfg.account_id, command.supersedes_operation_id)):
                            raise StorageConflict('PG_LEGACY_APPLYING_SUCCESSOR_INVALID')
                    elif await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.legacy_applying_imports WHERE account_id=$1)', cfg.account_id):
                        raise StorageConflict('PG_LEGACY_APPLYING_ORIGIN_ALREADY_IMPORTED')
                    if source_store is None and parent is not None:
                        source = LegacyApplyingSource.unpack(base64.b64decode(parent['source'], validate=True))
                        retirement, retired_at = parent['retirement'], parent['retired_at']
                    elif type(source_store) is not LegacySpotStore:
                        raise StorageError('PG_LEGACY_APPLYING_RETIRED_STORE_REQUIRED')
                    else:
                        source, retirement, retired_at = await source_store.frozen_legacy_applying_frame(source_store.binding)
                    expected.update(source=base64.b64encode(source.pack()).decode(), retirement=retirement, retired_at=retired_at)
                    if parent is not None and any(expected[k] != parent[k] for k in ('source', 'retirement', 'retired_at', 'source_verifier_policy')):
                        raise StorageConflict('PG_LEGACY_APPLYING_SUCCESSOR_INVALID')
                    now = await c.fetchval('SELECT clock_timestamp()')
                    _, policy = self._audit(expected, now)
                    key_policy = await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2', cfg.account_id, self.binding.config_hash)
                    key_policy = json.loads(key_policy)
                    if (key_policy.get('operator_verifier') != self.verifier.policy()
                            or key_policy.get('legacy_source_verifier') != self.source_verifier.policy()):
                        raise StorageError('PG_LEGACY_APPLYING_KEY_POLICY_MISMATCH')
                    if (await self.financial.observation_cut(self.binding)).cut != cut:
                        raise StorageConflict('PG_LEGACY_APPLYING_CUT_STALE')
                    bounds = await c.fetchrow('SELECT * FROM life_financial.legacy_applying_policies WHERE account_id=$1 AND policy_hash=$2', cfg.account_id, self.binding.policy_hash)
                    if bounds is None or bounds['max_fact_age_ms'] != self.max_fact_age_ms:
                        raise StorageError('PG_LEGACY_APPLYING_POLICY_MISMATCH')
                    if not json.loads(account)['halted'] or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self.max_fact_age_ms:
                        raise StorageError('PG_LEGACY_APPLYING_HALTED_FRESH_TARGET_REQUIRED')
                    native = await c.fetchrow('SELECT * FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
                    if (native is None or any(Decimal(policy[k]) != native[n] for k, n in (
                            ('opening_base_life', 'opening_base'), ('opening_quote_usdt', 'opening_quote'), ('opening_independent_price_usdt', 'opening_price')))
                            or datetime.fromisoformat(policy['opened_at']) != native['opened_at'] or policy['anchor_bill_id'] != native['anchor_bill']):
                        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')
                    expected.update(account=json.loads(account), source_basis=policy)
                    if not await c.fetchval('SELECT life_financial.legacy_cashflow_basis_valid($1,$2::jsonb)', cfg.account_id, json.dumps(policy)):
                        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_CASHFLOW_BASIS_REQUIRED')
                    if 'spot_preparations' in policy and not await c.fetchval('SELECT life_financial.legacy_spot_basis_valid($1,$2::jsonb,$3)',
                                                                              cfg.account_id, json.dumps(policy), cut.checkpoint.revision):
                        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_SPOT_BASIS_REQUIRED')
                    if 'swap_opening' in policy and not await c.fetchval('SELECT life_financial.legacy_joint_basis_valid($1,$2::jsonb,$3)',
                                                                         cfg.account_id, json.dumps(policy), cut.checkpoint.revision):
                        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_SWAP_BASIS_REQUIRED')
                    raw = json.dumps(expected, sort_keys=True, separators=(',', ':'))
                    if len(raw.encode()) > 4194304:
                        raise StorageError('PG_LEGACY_APPLYING_BOUND')
                    await c.execute('''INSERT INTO life_financial.legacy_applying_imports
                        (account_id,operation_id,source_digest,body,body_hash,captured_at,supersedes_operation_id) VALUES($1,$2,$3,$4::jsonb,
                        encode(sha256(convert_to(($4::jsonb)::text,'UTF8')),'hex'),$5,$6)''', cfg.account_id, command.operation_id, command.source_digest, raw, now, command.supersedes_operation_id)
                    if source_store is not None and await source_store.frozen_legacy_applying_frame(source_store.binding) != (source, retirement, retired_at):
                        raise StorageError('PG_LEGACY_APPLYING_SOURCE_CHANGED')
                    committing = True
                    await self._commit(tx)
                    self._original()
            except BaseException as error:
                import asyncpg
                try:
                    if not c.is_closed() and c.is_in_transaction():
                        await tx.rollback()
                except Exception:
                    pass
                if isinstance(error, (asyncpg.SerializationError, asyncpg.DeadlockDetectedError)):
                    raise StorageConflict('PG_LEGACY_APPLYING_RETRY_REQUIRED') from None
                if isinstance(error, asyncpg.PostgresError) and error.sqlstate and (error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                    raise sql_error(error) from None
                if committing:
                    raise CommitUnknown('PG_LEGACY_APPLYING_COMMIT_UNKNOWN') from None
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageUnavailable('PG_LEGACY_APPLYING_ABORTED') from error

    async def import_pending(self, command, *, source_store, approval, cut, facts):
        await self._approve(command, source_store, approval, cut, facts)
        version, state_digest = self.financial._cut(cut, self.owner)
        body = command.batch.json()
        request = dict(kind='JOINT_BATCH', binding=self.binding.json(), owner=self.owner.json(), version=version, cut=state_digest, command=body)
        self.runtime._ingress_epoch += 1
        return await self.runtime._transaction('SELECT life_financial.legacy_applying_pending($1,$2,$3,$4,$5,$6,$7)',
                                               (self.runtime.config.account_id, self.binding.json(), self.owner.json(), version, state_digest, fingerprint(request), body),
                                               self.owner, self.batch._receipt, observation=True)

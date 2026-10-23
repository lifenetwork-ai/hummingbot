"""Bounded, read-only telemetry; never permission, reconciliation or audit proof.

One separate connection, no queue, no sender/safety pool usage. Status reads
copy a local snapshot. SQL failure cannot revoke or re-arm financial authority.
Unknown audit/backup verification is explicit, including in an empty database.
"""
import asyncio
import copy
from decimal import Decimal, localcontext
from time import monotonic

from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import _ssl_context
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig

AUDIT_SQL = '''SELECT through_sequence,independent_signature IS NOT NULL AS signature_present
    FROM life_financial.audit_checkpoints WHERE account_id=$1
    ORDER BY through_sequence DESC,checkpoint_id LIMIT 1'''


def _amount(value):
    result = format(value, 'f')
    return result.rstrip('0').rstrip('.') if '.' in result else result


class PgFinancialMonitor:
    def __init__(self, config, *, max_rows, timeout_ms):
        if (type(config) is not PgConfig or type(max_rows) is not int or not 1 <= max_rows <= 256
                or type(timeout_ms) is not int or not 1 <= timeout_ms <= 2000):
            raise ValueError('FINANCIAL_TELEMETRY_CONFIG_INVALID')
        self.config, self.max_rows, self.timeout_ms = config, max_rows, timeout_ms
        self._pool, self._busy, self._closed = None, False, False
        self._sampled_at, self._snapshot = None, None
        self._state = 'NOT_SAMPLED'
        self._task = None

    def status(self):
        result = {} if self._snapshot is None else copy.deepcopy(self._snapshot)
        result.update(state=self._state, trading_permission=False,
                      sample_age_ms=None if self._sampled_at is None else round((monotonic() - self._sampled_at) * 1000, 3),
                      reason_code='FINANCIAL_TELEMETRY_CURRENT' if self._state == 'CURRENT' else 'FINANCIAL_TELEMETRY_UNAVAILABLE',
                      audit_health='UNVERIFIED', backup_health='UNVERIFIED', recovery_health='UNVERIFIED')
        return result

    async def _open(self):
        import asyncpg
        cfg = self.config
        ssl = await asyncio.to_thread(_ssl_context, cfg.dsn, cfg.local_insecure, cfg.ssl_ca_file)
        if self._closed:
            raise ValueError('FINANCIAL_TELEMETRY_CLOSED')
        self._pool = await asyncpg.create_pool(
            cfg.dsn, ssl=ssl, min_size=1, max_size=1, timeout=self.timeout_ms / 1000,
            command_timeout=self.timeout_ms / 1000,
            server_settings={'application_name': 'life_financial_monitor', 'default_transaction_read_only': 'on',
                             'statement_timeout': str(self.timeout_ms), 'idle_in_transaction_session_timeout': str(self.timeout_ms)})
        if self._closed:
            self._pool.terminate()
            self._pool = None
            raise ValueError('FINANCIAL_TELEMETRY_CLOSED')

    async def _collect(self):
        if self._closed:
            raise ValueError('FINANCIAL_TELEMETRY_CLOSED')
        if self._pool is None:
            await self._open()
        if self._closed:
            raise ValueError('FINANCIAL_TELEMETRY_CLOSED')
        async with self._pool.acquire() as c:
            async with c.transaction(isolation='repeatable_read', readonly=True):
                return await self._read(c)

    async def sample(self):
        if self._closed or self._busy or self._task is not None and not self._task.done():
            return
        self._busy = True
        try:
            # A deadline bounds the caller, not the physical TLS thread or
            # driver IO. Retain that task until joined; never queue a second
            # sample or publish a result that arrived after its caller expired.
            self._task = asyncio.create_task(self._collect())
            self._task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            snapshot = await asyncio.wait_for(asyncio.shield(self._task), self.timeout_ms / 1000)
            if not self._closed:
                self._snapshot, self._sampled_at, self._state = snapshot, monotonic(), 'CURRENT'
        except asyncio.CancelledError:
            self._state = 'UNAVAILABLE'
            if asyncio.current_task().cancelling() or not self._task.cancelled():
                raise
        except Exception:
            # Retain the prior snapshot with its original age and failed state;
            # never emit a DSN, SQL, exception payload or false zero on failure.
            self._state = 'UNAVAILABLE'
        finally:
            self._busy = False
            if self._closed:
                self._state = 'CLOSED'

    async def _read(self, c):
        cfg, cap = self.config, self.max_rows
        head = await c.fetchrow('''SELECT a.mode,a.halted,a.owner_generation,a.owner_epoch,a.version,
            a.current_session,a.database_lineage,a.lineage,a.policy_hash,a.config_hash,
            a.environment,a.exchange,a.account_uid,a.risk_pool,
            m.lineage AS storage_lineage,m.restore_operation_id,p.campaign_id
            FROM life_financial.accounts a CROSS JOIN life_storage.storage_metadata m
            LEFT JOIN life_financial.spot_policies p ON p.account_id=a.account_id AND p.policy_hash=a.policy_hash
            WHERE a.account_id=$1''', cfg.account_id)
        binding = cfg.binding
        if (head is None or head['database_lineage'] != cfg.database_lineage or head['storage_lineage'] != cfg.database_lineage
                or any(str(head[key]) != str(getattr(binding, key)) for key in
                       ('lineage', 'policy_hash', 'config_hash', 'environment', 'exchange', 'account_uid', 'risk_pool'))):
            raise ValueError('FINANCIAL_TELEMETRY_SCOPE_MISMATCH')
        # Match the partial-index predicates. LIMIT applies before Python
        # aggregation and before any historical terminal rows can be visited.
        attempts = await c.fetch('''SELECT state FROM life_financial.send_attempts
            WHERE account_id=$1 AND state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
            ORDER BY product,intent_id LIMIT $2''', cfg.account_id, cap + 1)
        holds = await c.fetch('''SELECT asset,remaining FROM life_financial.reservations
            WHERE account_id=$1 AND state<>'TERMINAL' ORDER BY asset,reservation_id LIMIT $2''', cfg.account_id, cap + 1)
        claims = await c.fetch('''SELECT claim_id FROM life_financial.capital_claims
            WHERE account_id=$1 AND state='HELD' ORDER BY product,claim_id LIMIT $2''', cfg.account_id, cap + 1)
        shared = await c.fetch('''SELECT intent_id FROM life_financial.shared_capital_claims
            WHERE account_id=$1 ORDER BY intent_id LIMIT $2''', cfg.account_id, cap + 1)
        balances = await c.fetch('''SELECT asset,total,reserved,liability FROM life_financial.balances
            WHERE account_id=$1 ORDER BY asset LIMIT 3''', cfg.account_id)
        # Nine exact primary-key probes: no OR/range over accumulated daily or
        # retired-session history. Missing buckets are absent, never assumed 0.
        budgets = await c.fetch('''SELECT b.family,b.scope,b.budget_limit,b.committed,b.incurred
            FROM (VALUES ('LOSS'),('SUBSIDY'),('STRESS')) f(family)
            CROSS JOIN (VALUES ('SESSION',$2::text),('CAMPAIGN',$3::text),
                ('DAY',to_char(transaction_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD'))) w(scope,window_key)
            JOIN life_financial.budget_buckets b ON b.account_id=$1 AND b.family=f.family
                AND b.scope=w.scope AND b.window_key=w.window_key ORDER BY b.family,b.scope''',
                                cfg.account_id, None if head['current_session'] is None else str(head['current_session']), head['campaign_id'])
        audit = await c.fetchrow(AUDIT_SQL, cfg.account_id)
        latest = await c.fetchval('''SELECT sequence FROM life_financial.financial_events
            WHERE account_id=$1 ORDER BY sequence DESC LIMIT 1''', cfg.account_id)

        def bounded(rows):
            return dict(count=min(len(rows), cap), truncated=len(rows) > cap)
        unknown = [row for row in attempts[:cap] if row['state'] in ('ARMED', 'SEND_UNKNOWN')]
        with localcontext() as context:
            context.prec = 80
            ratios = [(r['committed'] + r['incurred']) / r['budget_limit'] for r in budgets if r['budget_limit'] > 0]
        return dict(owner_generation=None if head['owner_generation'] is None else str(head['owner_generation']),
                    owner_epoch=head['owner_epoch'], financial_version=head['version'], mode=head['mode'], halted=head['halted'],
                    restore_quarantined=head['restore_operation_id'] is not None,
                    active_attempts=bounded(attempts),
                    unknown_attempts=dict(count=len(unknown), truncated=len(attempts) > cap),
                    unresolved_holds=bounded(holds),
                    capital_claims=bounded(claims), shared_claims=bounded(shared),
                    budget_usage_ratio_max=None if not ratios else float(max(ratios)),
                    latest_financial_sequence=latest,
                    audit_recorded_sequence=None if audit is None else audit['through_sequence'],
                    audit_signature_present=False if audit is None else audit['signature_present'],
                    audit_sequence_gap=None if audit is None or latest is None else latest - audit['through_sequence'],
                    balances={r['asset']: {k: _amount(r[k]) for k in ('total', 'reserved', 'liability')} for r in balances},
                    budgets=[dict(family=r['family'], scope=r['scope'], limit=_amount(r['budget_limit']),
                                  committed=_amount(r['committed']), incurred=_amount(r['incurred']),
                                  usage_ratio=_amount((r['committed'] + r['incurred']) / r['budget_limit'])
                                  if r['budget_limit'] > Decimal(0) else None) for r in budgets])

    async def close(self):
        self._closed, self._state = True, 'CLOSED'
        if self._pool is not None:
            # Only read-only diagnostic work owns this pool; terminating it
            # cannot cancel a financial COMMIT or release sender capacity.
            self._pool.terminate()
            self._pool = None
        if self._task is not None:
            try:
                await asyncio.shield(self._task)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling() or not self._task.cancelled():
                    raise
            except Exception:
                pass

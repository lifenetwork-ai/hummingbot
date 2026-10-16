"""Standalone SPOT read/prepare/arm ports; no selector, release or send authority."""
from datetime import datetime
from decimal import Decimal as D, InvalidOperation, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    ResourceVersion,
    StateCut,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AttemptState,
    FinancialCut,
    PrepareReceipt,
    PrepareSpot,
    SpotAttempt,
    SpotBalance,
    SpotBudget,
    SpotReservation,
    SpotSession,
    SpotSnapshot,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


class PgSpotRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, runtime):
        if type(runtime) is not PgRuntime:
            raise StorageError('PG_SPOT_RUNTIME_REQUIRED')
        self._runtime = runtime
        self.binding = runtime.config.binding

    def _binding(self, binding):
        if binding != self.binding:
            raise StorageError('PG_SPOT_BINDING_MISMATCH')

    def _cut(self, cut, owner):
        if type(cut) is not FinancialCut or type(owner) is not OwnerToken or not self._runtime.config.write:
            raise StorageError('PG_SPOT_COMMAND_INVALID')
        self._binding(cut.checkpoint.binding)
        version = cut.checkpoint.revision
        if (cut.checkpoint.event_version != version
                or cut.checkpoint.resource_versions != (ResourceVersion('account', version),)
                or cut.components != (('postgres_account', cut.checkpoint.state_digest),)):
            raise StorageError('PG_SPOT_CUT_INVALID')
        return version, cut.checkpoint.state_digest

    def _receipt(self, value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('SPOT_PREPARE', 'SPOT_ARM'):
            raise StorageUnavailable('PG_SPOT_RECEIPT_INVALID')
        return PrepareReceipt(self.binding, value['operation_id'], value['intent_id'], value['state'], value['replayed'], self.atomic_scope)

    async def snapshot(self, binding):
        self._binding(binding)

        async def read(c):
            return await c.fetchval('SELECT life_financial.spot_state($1)', self._runtime.config.account_id)
        value = await self._runtime._admit(read, safety=True)
        try:
            # Schema allows 38 integer + 18 fractional digits. Never use the
            # ambient application's smaller Decimal context for financial reads.
            with localcontext() as context:
                context.prec = 120
                return self._snapshot(value, binding)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise StorageUnavailable('PG_SPOT_STATE_INVALID') from None

    def _snapshot(self, value, binding):
        a, s, p = value['account'], value['session'], value['policy']
        b, q = value['balances']['LIFE'], value['balances']['USDT']
        # SQL returns all monetary fields as exact decimal strings.
        budgets = {(row['family'], row['scope']): row for row in value['budgets']}

        def amount(family, scope, key):
            return D(budgets.get((family, scope), {}).get(key, '0'))
        subsidy = {scope: amount('SUBSIDY', scope, 'committed') + amount('SUBSIDY', scope, 'incurred') for scope in ('SESSION', 'DAY', 'CAMPAIGN')}
        loss = {scope: amount('LOSS', scope, 'incurred') for scope in ('SESSION', 'DAY', 'CAMPAIGN')}
        limits = {scope: D(str(p['subsidy_' + scope.lower()])) for scope in subsidy}
        exhausted = any(loss[scope] + amount('LOSS', scope, 'committed') >= D(str(p['loss_' + scope.lower()])) for scope in loss)
        cut = StateCut(binding, a['version'], a['version'], (ResourceVersion('account', a['version']),), value['digest'])
        observed = datetime.fromisoformat(value['observed_at'])
        started, expires = datetime.fromisoformat(s['started_at']), datetime.fromisoformat(s['expires_at'])
        active = a['mode'] == 'ACTIVE' and not a['halted'] and started <= observed < expires
        if any(type(v) is not str for v in s['anchors'].values()):
            raise StorageUnavailable('PG_SPOT_ANCHOR_ENCODING_INVALID')
        anchors = {k: D(v) for k, v in s['anchors'].items()}
        if any(not v.is_finite() or v <= 0 for v in anchors.values()):
            raise StorageUnavailable('PG_SPOT_ANCHOR_VALUE_INVALID')
        return SpotSnapshot(
            FinancialCut(cut, (('postgres_account', value['digest']),)),
            SpotSession(s['session_id'], s['epoch'], s['config_version'], started, expires,
                        'ACTIVE' if active else 'PAUSED', started, False, anchors),
            SpotBalance(D(b['total']), D(q['total']), D(b['reserved']), D(q['reserved']),
                        max(D('0'), D(b['total']) - D(b['reserved']) - D(b['liability'])),
                        max(D('0'), D(q['total']) - D(q['reserved']) - D(q['liability'])), D(b['fees']), D(q['fees'])),
            SpotBudget(subsidy['SESSION'], subsidy['DAY'], subsidy['CAMPAIGN'],
                       max(D('0'), min(limits[k] - subsidy[k] for k in limits)),
                       loss['SESSION'], loss['DAY'], loss['CAMPAIGN'], exhausted),
            value['recovery_required'])

    async def session(self, binding):
        return (await self.snapshot(binding)).session

    async def balance(self, binding):
        return (await self.snapshot(binding)).balance

    async def budget(self, binding):
        return (await self.snapshot(binding)).budget

    async def _attempt(self, intent_id, binding):
        self._binding(binding)
        identifier(intent_id)

        async def read(c):
            return await c.fetchrow('''SELECT t.*,d.quote_level,d.operation_id AS prepare_id,
                r.amount AS principal_amount,r.remaining AS principal_remaining,r.state AS principal_state,
                CASE WHEN t.side='BUY' THEN t.quantity*t.price ELSE t.quantity END AS principal_expected
                FROM life_financial.send_attempts t
                JOIN life_financial.spot_preparations d USING(account_id,intent_id)
                LEFT JOIN life_financial.reservations r ON r.account_id=t.account_id AND r.reservation_id=d.operation_id||':principal'
                WHERE t.account_id=$1 AND t.intent_id=$2 AND t.product='SPOT' ''', self._runtime.config.account_id, intent_id)
        return await self._runtime._admit(read, safety=True)

    async def attempt(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        return SpotAttempt(intent_id, row['wire_id'], intent_id, str(row['session_id']), row['epoch'],
                           AttemptState.SEND_UNKNOWN if row['state'] == 'ARMED' else AttemptState(row['state']),
                           row['exchange_order_id'], False, 'LIFE-USDT', row['side'], row['quote_level'])

    async def reservation(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        # Only unfilled preparation is supported; later ports must qualify fill/release reads.
        if (row['state'] not in ('PREPARED', 'ARMED', 'SEND_UNKNOWN') or row['principal_state'] != 'HELD'
                or row['principal_remaining'] != row['principal_expected'] or row['principal_amount'] != row['principal_expected']):
            raise StorageUnavailable('PG_SPOT_RESERVATION_STATE_UNSUPPORTED')
        return SpotReservation(intent_id, row['side'], str(row['session_id']), row['epoch'], row['quantity'], row['quantity'], D('0'), row['price'], 'OPEN')

    async def lookup_prepare(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchrow('''SELECT o.receipt,t.state FROM life_financial.operations o
                JOIN life_financial.spot_preparations d USING(account_id,operation_id)
                JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE o.account_id=$1 AND o.operation_id=$2''', self._runtime.config.account_id, operation_id)
        row = await self._runtime._admit(read, safety=True)
        return None if row is None else self._receipt({**row['receipt'], 'state': row['state'], 'replayed': True})

    async def prepare(self, command, *, cut, owner):
        if type(command) is not PrepareSpot:
            raise StorageError('PG_SPOT_COMMAND_INVALID')
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': 'SPOT_PREPARE', 'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction('SELECT life_financial.spot_prepare($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt)

    async def arm(self, operation_id, *, cut, owner):
        identifier(operation_id)
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': 'SPOT_ARM', 'prepare': operation_id, 'cut': cut.json(), 'owner': owner.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, operation_id)
        return await self._runtime._transaction('SELECT life_financial.spot_arm($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt)

    async def close(self):
        await self._runtime.close()

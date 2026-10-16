"""Atomic standalone PostgreSQL inventory settlement; no sender or activation."""
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory, SpotSettlementReceipt


class PgInventorySettlementRepository:
    def __init__(self, financial):
        if type(financial) is not PgSpotRepository:
            raise StorageError('PG_SETTLEMENT_CONTEXT_INVALID')
        self.financial, self.runtime, self.binding = financial, financial._runtime, financial.binding

    def _receipt(self, value):
        if value.get('kind') != 'SPOT_INVENTORY_SETTLE' or value.get('trading_permission') is not False or value.get('state') != 'SETTLED':
            raise StorageUnavailable('PG_SETTLEMENT_RECEIPT_INVALID')
        return SpotSettlementReceipt(self.binding, value['operation_id'], value['cycle_id'],
                                     {key: Decimal(amount) for key, amount in value['actuals_usdt'].items()},
                                     value['proof_digest'], 'SETTLED', value['replayed'], self.financial.atomic_scope)

    async def _settle(self, command, facts, cut, owner, resume):
        if type(command) is not SettleSpotInventory or type(facts) is not SpotFinancialRecoveryFacts or len(command.intent_ids) > 64:
            raise StorageError('PG_SETTLEMENT_COMMAND_INVALID')
        self.financial._binding(facts.binding)
        version, digest = self.financial._cut(cut, owner)
        identity = fingerprint({'kind': 'SPOT_INVENTORY_SETTLE', 'binding': self.binding.json(), 'owner': owner.json(), 'command': command.json()})
        args = (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json(), facts.json(), resume)
        return await self.runtime._transaction('SELECT life_financial.spot_settle_inventory($1,$2,$3,$4,$5,$6,$7,$8,$9)',
                                               args, owner, self._receipt, observation=True)

    async def settle_inventory(self, command, *, facts, cut, owner):
        return await self._settle(command, facts, cut, owner, False)

    async def resume_inventory_settlement(self, command, *, facts, cut, owner):
        return await self._settle(command, facts, cut, owner, True)

    async def lookup_inventory_settlement(self, operation_id, binding):
        self.financial._binding(binding)
        identifier(operation_id)

        async def read(c):
            row = await c.fetchrow('''WITH current_state AS MATERIALIZED(SELECT life_financial.spot_state($1) AS value)
                SELECT value->>'unverified' AS unverified, o.receipt FROM current_state
                LEFT JOIN life_financial.operations o ON o.account_id=$1 AND o.operation_id=$2 AND o.command->>'kind'='SPOT_INVENTORY_SETTLE' ''',
                                   self.runtime.config.account_id, operation_id)
            if row['unverified'] != 'false':
                raise StorageUnavailable('PG_SETTLEMENT_UNVERIFIED')
            return row['receipt']
        value = await self.runtime._admit(read, safety=True)
        return None if value is None else self._receipt({**value, 'replayed': True})

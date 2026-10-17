"""Native paid-funding cash application, without expected-income credit.

The complete carried bundle is supplied by a separately qualified source. These
DTOs do not authenticate its account, bill scope or exchange observations.
"""
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.carry_monitor import (
    CarryAccount,
    CarryBundle,
    CarryFunding,
    CarryPrice,
    CarryTerms,
    FundingPayment,
)
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim, CapitalSnapshot, MarginTier, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    StateBinding,
    StorageError,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal, signed_decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


@dataclass(frozen=True)
class ApplyFunding:
    operation_id: str
    bundle: CarryBundle

    def __post_init__(self):
        identifier(self.operation_id)
        if len(self.operation_id) > 96 or type(self.bundle) is not CarryBundle:
            raise StorageError('PG_FUNDING_COMMAND_INVALID')

    def json(self):
        return dict(operation_id=self.operation_id, bundle=_json(asdict(self.bundle)))

    @classmethod
    def from_json(cls, data):
        def scalars(kind, raw):
            values = dict(raw)
            for definition in fields(kind):
                if definition.type is Decimal:
                    if type(values[definition.name]) is not str:
                        raise StorageError('PG_FUNDING_COMMAND_INVALID')
                    values[definition.name] = signed_decimal(Decimal(values[definition.name]))
            return values
        try:
            value = dict(data['bundle'])
            account = dict(value['account'])
            snapshot = scalars(CapitalSnapshot, account['snapshot'])
            snapshot['pending'] = tuple(CapitalClaim(**scalars(CapitalClaim, v)) for v in snapshot['pending'])
            account.update(snapshot=CapitalSnapshot(**snapshot), included_funding_bill_ids=tuple(account['included_funding_bill_ids']))
            value['account'] = CarryAccount(**account)
            for name in ('mark', 'index'):
                value[name] = CarryPrice(**scalars(CarryPrice, value[name]))
            funding = scalars(CarryFunding, value['funding'])
            funding['payments'] = tuple(FundingPayment(**scalars(FundingPayment, v)) for v in funding['payments'])
            value['funding'] = CarryFunding(**funding)
            terms = scalars(CarryTerms, value['terms'])
            terms['tiers'] = tuple(MarginTier(**scalars(MarginTier, v)) for v in terms['tiers'])
            value['terms'] = CarryTerms(**terms)
            return cls(data['operation_id'], CarryBundle(**value))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise StorageError('PG_FUNDING_COMMAND_INVALID') from None


@dataclass(frozen=True)
class FundingReceipt:
    binding: StateBinding
    operation_id: str
    state: str
    cash_delta_usdt: Decimal
    gross_debits_usdt: Decimal
    replayed: bool
    atomic_scope: AtomicScope = field(default=AtomicScope.ACCOUNT, init=False)
    trading_permission: bool = field(default=False, init=False)


class PgFundingRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, swap):
        if type(swap) is not PgSwapRepository:
            raise StorageError('PG_FUNDING_CONTEXT_INVALID')
        self.swap, self.financial, self.runtime = swap, swap.financial, swap.runtime
        self.binding, self.owner = swap.binding, swap.owner

    def _original(self):
        self.swap._original()
        if (self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.swap.binding != self.binding or self.swap.owner != self.owner):
            raise StorageError('PG_FUNDING_CONTEXT_CHANGED')

    def _receipt(self, value):
        self._original()
        if (value.get('kind') != 'PAID_FUNDING' or value.get('trading_permission') is not False
                or value.get('state') not in ('APPLIED', 'RECOVERY_REQUIRED') or type(value.get('replayed')) is not bool):
            raise StorageError('PG_FUNDING_RECEIPT_INVALID')
        return FundingReceipt(self.binding, value['operation_id'], value['state'], signed_decimal(Decimal(value['cash_delta_usdt'])),
                              decimal(Decimal(value['gross_debits_usdt'])), value['replayed'])

    async def _read(self, operation_id, binding):
        self._original()
        self.financial._binding(binding)
        return await self.swap.capital._read('SELECT life_financial.funding_read($1,$2,$3,$4)',
                                             (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes, operation_id))

    async def lookup(self, operation_id, binding):
        identifier(operation_id)
        value = await self._read(operation_id, binding)
        return None if value is None else self._receipt({**value['receipt'], 'replayed': True})

    async def frame(self, binding):
        return await self._read(None, binding)

    async def apply_funding(self, command, *, cut, owner):
        self._original()
        if type(command) is not ApplyFunding or owner != self.owner:
            raise StorageError('PG_FUNDING_CONTEXT_INVALID')
        version, digest = self.financial._cut(cut, owner)
        body = command.json()
        request = dict(kind='PAID_FUNDING', binding=self.binding.json(), owner=owner.json(), version=version, cut=digest, command=body)
        self.runtime._ingress_epoch += 1
        try:
            return await self.runtime._transaction('SELECT life_financial.funding_command($1,$2,$3,$4,$5,$6,$7)',
                                                   (self.runtime.config.account_id, self.binding.json(), owner.json(), version, digest, fingerprint(request), body),
                                                   owner, self._receipt, observation=True)
        except StorageError as error:
            if str(error) == 'PG_FUNDING_OBSERVATION_BOUND':
                self.runtime._healthy = False
            raise

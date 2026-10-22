"""Account-wide signed collateral reconciliation against one native ledger cut.

Dedicated mode-2 USDT cross margin only. Missing liabilities are unknown, never
zero. REST receipt age is distinct from balance/position update time: an idle
account can have an old uTime. A report grants no activation, release or send.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal as D, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository


def _amount(value):
    if type(value) is not str or len(value) > 100 or re.fullmatch(r'-?([0-9]+(\.[0-9]+)?|\.[0-9]+)', value) is None:
        raise StorageError('PG_COLLATERAL_AMOUNT_INVALID')
    result = D(value)
    if not result.is_finite() or abs(result) >= D('1e38') or result.as_tuple().exponent < -28:
        raise StorageError('PG_COLLATERAL_AMOUNT_INVALID')
    return result


def _stamp(value, now):
    if type(value) is not str or re.fullmatch('[1-9][0-9]{0,14}', value) is None or int(value) > now:
        raise StorageError('PG_COLLATERAL_TIMESTAMP_INVALID')
    return int(value)


@dataclass(frozen=True)
class AccountCollateral:
    base_life: D
    cash_usdt: D
    long_base: D
    short_base: D
    long_entry: D
    short_entry: D
    collateral_usdt: D
    initial_margin_usdt: D
    maintenance_margin_usdt: D
    position_marks: tuple[tuple[str, D], ...]
    # Preserve update stamps across both full reads, even for an idle account.
    updates: tuple[tuple[str, int], ...]


def parse_account(config, balance, positions, *, account_uid, position_mode, leverage,
                  contract_value_life, lot_contracts, observed_at_ms):
    """Strict normalization only; calling the parser authenticates no provider."""
    try:
        with localcontext() as ctx:
            ctx.prec = 160
            return _parse(config, balance, positions, account_uid, position_mode, leverage,
                          contract_value_life, lot_contracts, observed_at_ms)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        raise StorageError('PG_COLLATERAL_RESPONSE_INVALID') from None


def _parse(config, balance, positions, uid, mode, leverage, contract_value, lot, now):
    if type(config) is not dict or config.get('uid') != uid:
        raise StorageError('PG_COLLATERAL_UID_MISMATCH')
    if (mode not in ('ONEWAY', 'HEDGE') or type(leverage) is not int or leverage <= 0
            or config.get('acctLv') != '2' or config.get('autoLoan') is not False
            or config.get('posMode') != ('net_mode' if mode == 'ONEWAY' else 'long_short_mode')):
        raise StorageError('PG_COLLATERAL_MODE_UNSUPPORTED')
    if (type(contract_value) is not D or type(lot) is not D or not contract_value.is_finite()
            or not lot.is_finite() or not 0 < contract_value < D('1e38') or not 0 < lot < D('1e38')
            or type(now) is not int or not 0 < now <= 253402300799999):
        raise StorageError('PG_COLLATERAL_CONTRACTS_INVALID')
    details = balance['details']
    if (type(details) is not list or len(details) != 2 or any(type(v) is not dict for v in details)
            or {v.get('ccy') for v in details} != {'LIFE', 'USDT'}):
        raise StorageError('PG_COLLATERAL_ASSET_SCOPE_INCOMPLETE')
    if (type(positions) is not list or len(positions) > (1 if mode == 'ONEWAY' else 2)
            or any(type(v) is not dict for v in positions)):
        raise StorageError('PG_COLLATERAL_POSITION_SCOPE_INCOMPLETE')
    assets, stamps = {}, [('balance', _stamp(balance['uTime'], now))]
    for row in details:
        ccy = row['ccy']
        stamps.append((ccy, _stamp(row['uTime'], stamps[0][1])))
        for key in ('liab', 'crossLiab', 'isoLiab', 'uplLiab', 'interest'):
            if _amount(row.get(key)) != 0:
                raise StorageError('PG_COLLATERAL_LIABILITY_NONZERO')
        for key in ('isoEq', 'isoUpl', 'rewardBal', 'fixedBal'):
            if _amount(row.get(key)) != 0:
                raise StorageError('PG_COLLATERAL_UNSUPPORTED_BALANCE')
        assets[ccy] = {k: _amount(row.get(k)) for k in ('cashBal', 'eq', 'upl', 'imr', 'mmr')}
        if assets[ccy]['cashBal'] < 0 or assets[ccy]['eq'] < 0:
            raise StorageError('PG_COLLATERAL_LIABILITY_NONZERO')
        if assets[ccy]['eq'] != assets[ccy]['cashBal'] + assets[ccy]['upl']:
            raise StorageError('PG_COLLATERAL_EQUITY_MISMATCH')
    if any(assets['LIFE'][k] != 0 for k in ('upl', 'imr', 'mmr')):
        raise StorageError('PG_COLLATERAL_UNSUPPORTED_BALANCE')
    sides, marks, profit, imr, mmr = {}, [], D('0'), D('0'), D('0')
    for row in positions:
        side = row.get('posSide')
        if (row.get('instType') != 'SWAP' or row.get('instId') != 'LIFE-USDT-SWAP'
                or row.get('mgnMode') != 'cross' or row.get('ccy') != 'USDT'
                or side not in ({'net'} if mode == 'ONEWAY' else {'long', 'short'}) or side in sides):
            raise StorageError('PG_COLLATERAL_POSITION_SCOPE_INCOMPLETE')
        if _amount(row.get('lever')) != leverage:
            raise StorageError('PG_COLLATERAL_MODE_UNSUPPORTED')
        contracts = _amount(row.get('pos'))
        if contracts % lot or mode == 'HEDGE' and contracts < 0:
            raise StorageError('PG_COLLATERAL_CONTRACTS_INVALID')
        entry = D('0') if contracts == 0 and row.get('avgPx') == '' else _amount(row.get('avgPx'))
        mark, upl = _amount(row.get('markPx')), _amount(row.get('upl'))
        if mark <= 0 or entry < 0 or contracts != 0 and entry <= 0:
            raise StorageError('PG_COLLATERAL_AMOUNT_INVALID')
        signed = contracts * contract_value * (-1 if side == 'short' else 1)
        if upl != signed * (mark - entry):
            raise StorageError('PG_COLLATERAL_EQUITY_MISMATCH')
        initial, maintenance = _amount(row.get('imr')), _amount(row.get('mmr'))
        if not 0 <= maintenance <= initial:
            raise StorageError('PG_COLLATERAL_MARGIN_MISMATCH')
        sides[side] = (signed, entry if signed else D('0'))
        marks.append((side, mark))
        stamps.append((side, _stamp(row['uTime'], now)))
        profit, imr, mmr = profit + upl, imr + initial, mmr + maintenance
    if profit != assets['USDT']['upl']:
        raise StorageError('PG_COLLATERAL_EQUITY_MISMATCH')
    if (imr, mmr) != (assets['USDT']['imr'], assets['USDT']['mmr']):
        raise StorageError('PG_COLLATERAL_MARGIN_MISMATCH')
    long, short = sides.get('long', (D('0'), D('0'))), sides.get('short', (D('0'), D('0')))
    if 'net' in sides:
        signed, entry = sides['net']
        long, short = (signed, entry) if signed > 0 else (D('0'), D('0')), (-signed, entry) if signed < 0 else (D('0'), D('0'))
    return AccountCollateral(assets['LIFE']['cashBal'], assets['USDT']['cashBal'], long[0], abs(short[0]), long[1], short[1],
                             assets['USDT']['eq'], imr, mmr, tuple(sorted(marks)), tuple(sorted(stamps)))


@dataclass(frozen=True)
class CollateralReconciliation:
    cut: FinancialCut
    collection_id: str
    source_hash: str
    account: AccountCollateral
    trading_permission: bool = field(default=False, init=False)


class PgCollateralReconciler:
    def __init__(self, reader, swap):
        if (type(reader) is not PgSpotAccountReader or type(swap) is not PgSwapRepository
                or reader.repository is not swap.financial or reader.runtime is not swap.runtime
                or reader.binding != swap.binding or reader.owner != swap.owner):
            raise StorageError('PG_COLLATERAL_CONTEXT_INVALID')
        self.reader, self.swap = reader, swap
        self.financial, self.runtime, self.binding = swap.financial, swap.runtime, swap.binding
        self._context = (reader, swap, self.financial, self.runtime, self.binding, swap.owner)

    def _original(self):
        reader, swap, financial, runtime, binding, owner = self._context
        if (self.reader is not reader or self.swap is not swap or self.financial is not financial
                or self.runtime is not runtime or self.binding != binding or swap.owner != owner):
            raise StorageError('PG_COLLATERAL_CONTEXT_CHANGED')
        self.swap._original()
        if (self.reader.repository is not self.financial or self.reader.runtime is not self.runtime
                or self.swap.financial is not self.financial or self.swap.runtime is not self.runtime
                or self.reader.binding != self.binding or self.swap.binding != self.binding
                or self.reader.owner != self.swap.owner):
            raise StorageError('PG_COLLATERAL_CONTEXT_CHANGED')

    async def _native(self):
        self._original()
        value = await self.swap.capital._read('SELECT life_financial.collateral_reconciliation_context($1,$2,$3)',
                                              (self.runtime.config.account_id, self.swap.capital.max_rows, self.swap.capital.max_bytes))
        return self._decode_native(value)

    def _decode_native(self, value):
        self._original()
        if any(value['snapshot']['account'][key] != field for key, field in self.binding.json().items()):
            raise StorageError('PG_COLLATERAL_CONTEXT_CHANGED')
        with localcontext() as ctx:
            ctx.prec = 160
            return value, self.financial._snapshot(value['snapshot'], self.binding)

    async def _sample(self, policy, *, account_brackets=False):
        if account_brackets:
            config = self.reader.account_configuration()
            balance, positions = await self.reader.read_batch((
                ('/api/v5/account/balance', {}), ('/api/v5/account/positions', {})))
        else:
            config, balance, positions = await self.reader.read_batch((
                ('/api/v5/account/config', {}), ('/api/v5/account/balance', {}), ('/api/v5/account/positions', {})))
        return self._parse_sample(config, balance, positions, policy)

    def _parse_sample(self, config, balance, positions, policy):
        if len(config['data']) != 1 or len(balance['data']) != 1:
            raise StorageError('PG_COLLATERAL_RESPONSE_INVALID')
        sample = parse_account(config['data'][0], balance['data'][0], positions['data'],
                               account_uid=self.binding.account_uid, position_mode=policy['position_mode'],
                               leverage=policy['leverage'], contract_value_life=D(policy['contract_value_life']),
                               lot_contracts=D(policy['lot_contracts']),
                               observed_at_ms=int(utc(self.reader.source.journal.clock()).timestamp() * 1000))
        return sample, {'account': config, 'balance': balance, 'positions': positions}

    def _match(self, original, view, current, after, account):
        if view.cut != after.cut or original['positions'] != current['positions']:
            raise StorageError('PG_COLLATERAL_NATIVE_CUT_CHANGED')
        self._match_account(original, view, account)

    def _match_account(self, original, view, account):
        legs = original['positions']
        if ((account.base_life, account.cash_usdt) != (view.balance.total_base_life, view.balance.total_quote_usdt)
                or (account.long_base, account.short_base, account.long_entry, account.short_entry) !=
                tuple(D(legs[k]) for k in ('long_base', 'short_base', 'long_entry', 'short_entry'))):
            raise StorageError('PG_COLLATERAL_NATIVE_MISMATCH')
        stamps = dict(account.updates)
        if (any(stamps[ccy] < at for ccy, at in original['money_update_ms'].items())
                or any(stamps[side] < at for side, at in original['position_update_ms'].items() if side in stamps)):
            raise StorageError('PG_COLLATERAL_TIMESTAMP_PREDATES_NATIVE')
        self.reader.source._credentials()
        if not self.reader.source._uid_fresh():
            raise StorageError('PG_COLLATERAL_SOURCE_EXPIRED')

    async def collect(self):
        """Observation-only; a new collection is required after any ledger change.

        Double full reads detect changes during the non-atomic REST sequence.
        They cannot prove no intervening change/revert at the exchange. Cold
        decoded journal pages never become signed-source or trading authority.
        """
        try:
            original, view = await self._native()
            policy = original['capital_policy']
            async with self.reader.collection():
                samples, payloads = [], []
                for _ in range(2):
                    sample, payload = await self._sample(policy)
                    samples.append(sample)
                    payloads.append(payload)
                if samples[0] != samples[1]:
                    raise StorageError('PG_COLLATERAL_SOURCE_CHANGED')
            current, after = await self._native()
            account = samples[0]
            self._match(original, view, current, after, account)
            return CollateralReconciliation(view.cut, self.reader.collection_id,
                                            fingerprint(payloads), account)
        except BaseException:
            self._context[3]._healthy = False
            raise

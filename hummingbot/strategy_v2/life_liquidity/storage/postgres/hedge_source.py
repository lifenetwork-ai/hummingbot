"""Fresh signed hedge observations over a fully reconciled native account.

Provider timestamps are retained. Only our two explicit observation writes may
advance the captured epoch/cut; unrelated ingress or a separate primary writer
invalidates the entire refresh. No source label, cold SQL readback or repeat
operation recreates this process-local capture. This class grants no HTTP send.
"""
import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal as D, localcontext

from hummingbot.strategy_v2.life_liquidity.carry_monitor import (
    CarryAccount,
    CarryBundle,
    CarryFunding,
    CarryPrice,
    CarryTerms,
    FundingPayment,
)
from hummingbot.strategy_v2.life_liquidity.hedge_coordinator import HedgeMarket
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot, MarginTier
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal, signed_decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import _amount
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import HedgeChild, PgHedgeRepository, PlanHedge
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
from hummingbot.strategy_v2.life_liquidity.storage.postgres.operation_preflight import preflight_new_operation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductCut
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap

ROUTES = (
    ('/api/v5/public/instruments', {'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP'}),
    ('/api/v5/market/books', {'instId': 'LIFE-USDT-SWAP', 'sz': '64'}),
    ('/api/v5/public/mark-price', {'instType': 'SWAP', 'instId': 'LIFE-USDT-SWAP'}),
    ('/api/v5/market/index-tickers', {'instId': 'LIFE-USDT'}),
    ('/api/v5/public/funding-rate', {'instId': 'LIFE-USDT-SWAP'}),
    ('/api/v5/public/position-tiers', {'instType': 'SWAP', 'tdMode': 'cross', 'instFamily': 'LIFE-USDT'}),
    ('/api/v5/account/trade-fee', {'instType': 'SWAP', 'instFamily': 'LIFE-USDT'}),
    ('/api/v5/account/leverage-info', {'instId': 'LIFE-USDT-SWAP', 'mgnMode': 'cross'}),
)


@dataclass(frozen=True)
class HedgeSourceReceipt:
    capital: object
    carry: object
    market: HedgeMarket
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class SpotAllocationReceipt:
    observations: HedgeSourceReceipt
    capital: object
    carry: object
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class _SpotAllocation:
    source_operation: str
    intent: object
    capital_operation: str
    carry_operation: str
    capital_read: object
    carry_read: object


class PgHedgeSource:
    def __init__(self, collector, *, carry, swap_source, max_levels, timeout_ms, available_edge_quote=D('0')):
        if (type(collector) is not PgJointAccountReconciler or type(carry) is not PgCarryRepository
                or carry.capital is not collector.swap.capital
                or type(swap_source) is not PgSwapAccountSource or swap_source.boundary.swap is not collector.swap
                or type(max_levels) is not int or not 0 < max_levels <= 64
                or type(timeout_ms) is not int or not 0 < timeout_ms <= 60000
                or type(available_edge_quote) is not D or not available_edge_quote.is_finite()
                or not 0 <= available_edge_quote < D('1e38')):
            raise StorageError('PG_HEDGE_SOURCE_CONFIG_INVALID')
        self.collector, self.carry, self.capital = collector, carry, carry.capital
        self.runtime, self.financial, self.owner = collector.runtime, collector.financial, collector.swap.owner
        self.binding, self.reader = collector.binding, collector.reader
        self.swap_source = swap_source
        self.max_levels, self.timeout_ms, self.available_edge_quote = max_levels, timeout_ms, available_edge_quote
        self._context = (collector, carry, self.capital, self.reader, self.runtime, self.financial, self.owner,
                         self.binding, max_levels, timeout_ms, available_edge_quote, swap_source)
        self._busy = self._stopped = False
        self._capture = None
        self._qualified = None
        self._planning = False
        self._plan_task = None
        self._allocating = False
        self._allocation_basis = None
        self._allocation_request = None
        self.capital.observation, self.carry.observation = self._capital_observe, self._carry_observe

    def assert_binding(self, *, observation=False):
        """Synchronous owning-context check, without recursing into its sender."""
        if type(observation) is not bool:
            raise StorageError('PG_HEDGE_SOURCE_CONTEXT_CHANGED')
        if self._context != (self.collector, self.carry, self.capital, self.reader, self.runtime, self.financial,
                             self.owner, self.binding, self.max_levels, self.timeout_ms, self.available_edge_quote,
                             self.swap_source):
            raise StorageError('PG_HEDGE_SOURCE_CONTEXT_CHANGED')
        self.collector._original()
        self.carry._original()
        self.reader.source._credentials()
        if self.reader.source._quota is not self.swap_source._quota:
            raise StorageError('PG_HEDGE_SOURCE_SDK_CONTEXT_CHANGED')
        if (self.capital.observation != self._capital_observe or self.carry.observation != self._carry_observe
                or self._stopped and not observation):
            raise StorageError('PG_HEDGE_SOURCE_STOPPED')

    def _original(self):
        self.assert_binding()
        if (self.swap_source.boundary.swap is not self.collector.swap or not self.swap_source._ready()
                or self.swap_source._order_url != self.reader.source.endpoint_url):
            raise StorageError('PG_HEDGE_SOURCE_SDK_CONTEXT_CHANGED')

    def assert_sdk_scope(self, child):
        """Recheck both SDKs at final send, allowing only our prepared child."""
        from hummingbot.core.data_type.common import OrderType
        if self.reader.source._connector._order_tracker.active_orders:
            raise StorageError('PG_HEDGE_SOURCE_SDK_SCOPE_CHANGED')
        active = self.swap_source._connector._order_tracker.active_orders
        if type(child) is not PrepareSwap or len(active) != 1:
            raise StorageError('PG_HEDGE_SOURCE_SDK_SCOPE_CHANGED')
        order, cfg = active.get(child.client_order_id), child.config()
        if (order is None or order.exchange_order_id is not None
                or order.client_order_id != child.client_order_id or order.trading_pair != cfg.trading_pair
                or order.order_type is not OrderType.LIMIT_MAKER or order.trade_type is not cfg.side
                or order.position is not cfg.position_action or order.leverage != cfg.leverage
                or order.amount != cfg.amount or order.price != cfg.price):
            raise StorageError('PG_HEDGE_SOURCE_SDK_SCOPE_CHANGED')

    def stop(self):
        if not self._stopped:
            self._stopped, self._capture, self._qualified = True, None, None
            self._allocation_basis = None
            self._context[4]._healthy = False
            self._context[4]._ingress_epoch += 1

    def _now(self):
        return int(utc(self.reader.source.journal.clock()).timestamp() * 1000)

    def _stamp(self, value, age):
        if (type(value) is not str or not value.isascii() or not value.isdecimal()
                or len(value) > 15 or not 0 <= self._now() - int(value) <= age):
            raise StorageError('PG_HEDGE_SOURCE_STALE')
        return int(value)

    @staticmethod
    def _one(value):
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise StorageError('PG_HEDGE_SOURCE_RESPONSE_SCOPE')
        return value[0]

    def _market(self, data, cp, policy, sequence, received):
        instrument, book, mark, index, funding = (self._one(v) for v in data[:5])
        terms = self._one(data[6])
        if (any(instrument.get(k) != v for k, v in dict(instType='SWAP', instId='LIFE-USDT-SWAP', instFamily='LIFE-USDT',
                                                        state='live', ctType='linear', ctValCcy='LIFE', settleCcy='USDT').items())
                or _amount(instrument['ctVal']) != D(cp['contract_value_life'])
                or _amount(instrument['lotSz']) != D(cp['lot_contracts'])
                or any(v.get('instId') != 'LIFE-USDT-SWAP' or v.get('instType') != 'SWAP' for v in (mark, funding))
                or index.get('instId') != 'LIFE-USDT' or terms.get('instType') != 'SWAP'):
            raise StorageError('PG_HEDGE_SOURCE_INSTRUMENT_SCOPE')
        from hummingbot.core.data_type.common import PositionMode
        sdk = self.swap_source._connector
        rule = sdk.trading_rules.get('LIFE-USDT')
        if (sdk._contract_sizes.get('LIFE-USDT') != _amount(instrument['ctVal'])
                or sdk.get_leverage('LIFE-USDT') != cp['leverage']
                or sdk.position_mode is not (PositionMode.HEDGE if cp['position_mode'] == 'HEDGE' else PositionMode.ONEWAY)
                or rule is None or rule.min_price_increment != _amount(instrument['tickSz'])
                or rule.min_base_amount_increment != _amount(instrument['lotSz']) * _amount(instrument['ctVal'])
                or rule.min_order_size != _amount(instrument['minSz']) * _amount(instrument['ctVal'])):
            raise StorageError('PG_HEDGE_SOURCE_SDK_METADATA_MISMATCH')
        leverage = data[7]
        sides = {'long', 'short'} if cp['position_mode'] == 'HEDGE' else {'net'}
        if (type(leverage) is not list or len(leverage) != len(sides)
                or any(type(row) is not dict for row in leverage)
                or {row.get('posSide') for row in leverage} != sides
                or any(row.get('instId') != 'LIFE-USDT-SWAP' or row.get('mgnMode') != 'cross'
                       or _amount(row.get('lever')) != cp['leverage'] for row in leverage)):
            raise StorageError('PG_HEDGE_SOURCE_EXCHANGE_LEVERAGE_MISMATCH')
        age = min(cp['max_age_ms'], policy['max_age_ms'], self.collector.max_age_ms)
        stamps = [self._stamp(v['ts'], age) for v in (book, mark, index, funding, terms)]
        if max(stamps + [received]) - min(stamps + [received]) > policy['max_skew_ms']:
            raise StorageError('PG_HEDGE_SOURCE_SKEW')
        mark_price, index_price = decimal(_amount(mark['markPx'])), decimal(_amount(index['idxPx']))
        if min(mark_price, index_price) <= 0:
            raise StorageError('PG_HEDGE_SOURCE_PRICE_INVALID')
        tiers = tuple(MarginTier(**{k: decimal(D(v)) for k, v in row.items()}) for row in cp['tiers'])
        # Fixed native tiers may be stricter than provider contract tiers. Their
        # notional caps must fit even at the configured adverse price endpoint.
        rows = data[5]
        if not 0 < len(rows) <= 64:
            raise StorageError('PG_HEDGE_SOURCE_TIER_SCOPE')
        previous = D('0')
        normalized = []
        for number, row in enumerate(rows, 1):
            lower, upper, imr, mmr = (_amount(row[k]) for k in ('minSz', 'maxSz', 'imr', 'mmr'))
            if (row.get('instFamily') != 'LIFE-USDT' or row.get('tier') != str(number)
                    or lower != previous or not lower < upper or not 0 < mmr <= imr <= 1):
                raise StorageError('PG_HEDGE_SOURCE_TIER_SCOPE')
            normalized.append((lower, upper, imr, mmr))
            previous = upper
        floor = min(mark_price, index_price) * (1 - D(cp['price_stress_bps']) / 10000) * D(cp['contract_value_life'])
        for tier in tiers:
            contracts = tier.max_notional_quote / floor
            # At an exact shared boundary, reserve the higher provider tier.
            relevant = [r for r in normalized if r[0] <= contracts]
            if (contracts > previous or not relevant
                    or tier.initial_margin_rate < max(D('1') / cp['leverage'], *(r[2] for r in relevant))
                    or tier.maintenance_margin_rate < max(r[3] for r in relevant)):
                raise StorageError('PG_HEDGE_SOURCE_TIER_UNDERESTIMATED')
        groups = terms.get('feeGroup')
        if (type(groups) is not list or not 0 < len(groups) <= 64
                or any(type(g) is not dict for g in groups)
                or len({g.get('groupId') for g in groups}) != len(groups)):
            raise StorageError('PG_HEDGE_SOURCE_FEE_SCOPE')
        matching = [g for g in groups if g.get('groupId') == instrument.get('groupId')]
        if len(matching) != 1 or not instrument.get('groupId'):
            raise StorageError('PG_HEDGE_SOURCE_FEE_SCOPE')
        maker, taker = (decimal(max(D('0'), -_amount(matching[0][k]))) for k in ('maker', 'taker'))
        if max(maker, taker) > 1:
            raise StorageError('PG_HEDGE_SOURCE_FEE_SCOPE')

        def levels(name):
            rows = book[name]
            if type(rows) is not list or not 0 < len(rows) <= 64:
                raise StorageError('PG_HEDGE_SOURCE_DEPTH_INVALID')
            result = []
            for row in rows:
                if type(row) is not list or len(row) != 4:
                    raise StorageError('PG_HEDGE_SOURCE_DEPTH_INVALID')
                price, size = _amount(row[0]), _amount(row[1])
                if min(price, size) <= 0:
                    raise StorageError('PG_HEDGE_SOURCE_DEPTH_INVALID')
                result.append((decimal(price), decimal(size * D(cp['contract_value_life']))))
            if any((a[0] <= b[0] if name == 'bids' else a[0] >= b[0]) for a, b in zip(result, result[1:])):
                raise StorageError('PG_HEDGE_SOURCE_DEPTH_INVALID')
            return tuple(result[:self.max_levels])
        bids, asks = levels('bids'), levels('asks')
        if bids[0][0] >= asks[0][0]:
            raise StorageError('PG_HEDGE_SOURCE_DEPTH_CROSSED')
        rate = signed_decimal(_amount(funding['fundingRate']))
        settlement, following = (funding[k] for k in ('fundingTime', 'nextFundingTime'))
        if (any(type(v) is not str or not v.isascii() or not v.isdecimal() or len(v) > 15 for v in (settlement, following))
                or not self._now() < int(settlement) < int(following) or abs(rate) > 1
                or max(int(settlement) - self._now(), int(following) - int(settlement)) > policy['max_schedule_gap_ms']):
            raise StorageError('PG_HEDGE_SOURCE_FUNDING_INVALID')
        return (HedgeMarket(self.binding.account_uid, 'LIFE-USDT-SWAP', sequence, min(stamps), bids, asks,
                            max(maker, taker), D('0'), decimal(self.available_edge_quote), True, 'okx_depth_fee'),
                CarryPrice('LIFE-USDT-SWAP', sequence, stamps[1], mark_price, 'okx_mark'),
                CarryPrice('LIFE-USDT-SWAP', sequence, stamps[2], index_price, 'okx_index'),
                rate, int(settlement), int(following), stamps[3],
                CarryTerms(self.binding.account_uid, 'LIFE-USDT-SWAP', sequence, stamps[4], maker, taker, tiers, True, 'okx_account_terms'))

    def _check(self, report, epoch, market):
        self._original()
        self.collector.check_report(report, epoch=epoch)
        if (report.runner_executor_ids or self.reader.source._connector._order_tracker.active_orders
                or self.swap_source._connector._order_tracker.active_orders
                or self.reader.source._quota is None and not self.swap_source._uid_fresh()):
            raise StorageError('PG_HEDGE_SOURCE_RUNNER_NOT_QUIESCENT')
        for row in report.orders:
            native, exchange = json.loads(row.native_json), None if row.exchange_json is None else json.loads(row.exchange_json)
            if native.get('retired_spot') is True and native['product'] == 'SPOT' and native['state'] == 'TERMINAL' and exchange is None:
                continue  # Verified permanent history, not a new terminal grant.
            if exchange is None or exchange['state'] not in ('filled', 'canceled') or native['state'] == 'PREPARED':
                raise StorageError('PG_HEDGE_SOURCE_ORDERS_NOT_RECONCILED')
        if not 0 <= self._now() - market.observed_at_ms <= self.collector.max_age_ms:
            raise StorageError('PG_HEDGE_SOURCE_STALE')

    def _capital_observe(self):
        if self._capture is None or self._capture[0] != 'capital':
            raise StorageError('PG_HEDGE_SOURCE_CAPTURE_REQUIRED')
        self._allocation_task()
        _, report, epoch, snapshot, bundle, market = self._capture
        self._check(report, epoch + 1, market)
        return snapshot

    def _carry_observe(self):
        if self._capture is None or self._capture[0] != 'carry':
            raise StorageError('PG_HEDGE_SOURCE_CAPTURE_REQUIRED')
        self._allocation_task()
        _, report, epoch, snapshot, bundle, market = self._capture
        self._check(report, epoch + 2, market)
        return bundle

    async def refresh(self, operation_id, *, source_report=None, _allocation=None):
        identifier(operation_id)
        if self._busy or self._planning and self._plan_task is not asyncio.current_task():
            raise StorageError('PG_HEDGE_SOURCE_BACKPRESSURE')
        self._busy = True
        cap_id, carry_id = ('hedge-source-' + k + ':' + fingerprint([operation_id]) for k in ('capital', 'carry'))
        try:
            self._original()
            if _allocation is not None:
                self._allocation_task()
                if (type(_allocation) is not _SpotAllocation or _allocation is not self._allocation_request
                        or _allocation.source_operation != operation_id or not self._allocating or source_report is not None):
                    raise StorageError('PG_SPOT_ALLOCATION_ORIGINAL_TASK_REQUIRED')
                cap_id, carry_id = _allocation.capital_operation, _allocation.carry_operation
            async with asyncio.timeout(self.timeout_ms / 1000), AsyncExitStack() as stack:
                quota = self.reader.source._quota
                if quota is not None and source_report is None:
                    await stack.enter_async_context(quota.collection())
                    self._original()
                if (self.reader.source._connector._order_tracker.active_orders
                        or self.swap_source._connector._order_tracker.active_orders):
                    raise StorageError('PG_HEDGE_SOURCE_RUNNER_NOT_QUIESCENT')
                cap_preflight = (await preflight_new_operation(self.capital, cap_id)
                                 if _allocation is None else _allocation.capital_read)
                carry_preflight = (await preflight_new_operation(self.carry, carry_id)
                                   if _allocation is None else _allocation.carry_read)
                if cap_preflight is None or carry_preflight is None:
                    raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
                with localcontext() as precision:
                    precision.prec = 160
                    cap, carry = await self.capital.frame(self.binding), await self.carry.frame(self.binding)
                    if cap['policy'] is None or carry['policy'] is None:
                        raise StorageError('PG_HEDGE_SOURCE_NATIVE_POLICY_REQUIRED')
                    hedge = await PgHedgeRepository(self.collector.swap).frame(self.binding)
                    swap_policy = (await self.collector.swap.frame(self.binding))['policy']
                    sequences = [0 if cap['snapshot'] is None else cap['snapshot']['sequence']]
                    if carry['bundle'] is not None:
                        sequences.extend(carry['bundle'][k]['sequence'] for k in ('mark', 'index', 'funding', 'terms'))
                    sequences.extend(row['progress']['market']['sequence'] for row in hedge['plans'])
                    sequence = 1 + max(sequences)
                    report = (await self.collector.collect(include_hedge_market=True)
                              if source_report is None else source_report)
                    epoch = self.collector._epoch
                    self.collector.check_report(report, epoch=epoch)
                    if report.market_json is None or report.market_observed_at is None:
                        raise StorageError('PG_HEDGE_SOURCE_MARKET_HANDOFF_REQUIRED')
                    received = int(report.market_observed_at.timestamp() * 1000)
                    data = json.loads(report.market_json)
                    market, mark, index, rate, settlement, following, funding_at, terms = self._market(data, cap['policy'], carry['policy'], sequence, received)
                    if quota is None:
                        # Unpooled SDKs may carry different keys for the same
                        # UID and must each prove that identity. An installed
                        # pool pins the exact same key/auth/endpoint in both
                        # SDKs; the signed collection already proves its UID.
                        # Do not copy/renew the SWAP send identity here: its
                        # original final HTTP guard still forces a fresh UID.
                        await self.swap_source.refresh_uid(force=True)
                    self._check(report, epoch, market)
                    if report.coverage_from_ms > carry['policy']['anchor_ms']:
                        raise StorageError('PG_HEDGE_SOURCE_FUNDING_COVERAGE_GAP')
                    account, cp = report.account, cap['policy']
                    # The private account and public mark may arrive at different
                    # instants. Never finance a new order with stale positive UPL
                    # or with a gain only present in the newer public mark.
                    marked = account.cash_usdt + account.long_base * (mark.price_usdt - account.long_entry)
                    marked += account.short_base * (account.short_entry - mark.price_usdt)
                    collateral = min(account.collateral_usdt, marked).quantize(D('1e-18'), rounding=ROUND_FLOOR)
                    if collateral < 0:
                        raise StorageError('PG_HEDGE_SOURCE_COLLATERAL_NEGATIVE')
                    snapshot = CapitalSnapshot(self.binding.account_uid, '2', 'USDT', cp['position_mode'], 'cross', cp['leverage'],
                                               int(report.account_observed_at.timestamp() * 1000), sequence, True, 'okx_account',
                                               decimal(account.base_life), decimal(account.cash_usdt), decimal(account.long_base), decimal(account.short_base),
                                               decimal(collateral), decimal(account.initial_margin_usdt), decimal(account.maintenance_margin_usdt),
                                               D('0'), mark.price_usdt, index.price_usdt, 'okx_mark', 'okx_index', ())
                    payments = tuple(FundingPayment(k, at, signed_decimal(amount)) for k, at, amount in report.funding_payments)
                    coverage = min(int(report.observed_at.timestamp() * 1000), funding_at)
                    bundle = CarryBundle(CarryAccount(snapshot, tuple(v.bill_id for v in payments), True, True), mark, index,
                                         CarryFunding(self.binding.account_uid, 'LIFE-USDT-SWAP', sequence, funding_at, rate,
                                                      settlement, following, carry['policy']['anchor_ms'], coverage, payments, True, 'okx_funding'), terms)
                    # The native capital command consumes this exact original
                    # cut. Do not replace it with an extra fresh snapshot read.
                    self._capture = ('capital', report, epoch, snapshot, bundle, market)
                    if _allocation is None:
                        capital = await self.capital._command(cap_id, None, ProductCut(report.cut.checkpoint, report.cut.components), self.owner,
                                                              _preflight=cap_preflight)
                    else:
                        from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
                        from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
                        intent = _allocation.intent
                        if intent.config_version != report.config_version:
                            raise StorageError('PG_SPOT_ALLOCATION_INTENT_INVALID')
                        claim = CapitalClaim(intent.intent_id, intent.wire_id, self.binding.account_uid,
                                             str(self.owner.session_id), self.owner.epoch, intent.config_version,
                                             report.risk_epoch, 'SPOT', intent.side.name, 'OPEN',
                                             intent.quantity_base, intent.price_usdt, 1)
                        # RESERVE already records and fully verifies this NEW
                        # signed snapshot and every existing/prospective claim.
                        # A separate OBSERVE/carry pair adds no missing check.
                        capital = await self.capital.reserve(ReserveCapital(cap_id, claim),
                                                             ProductCut(report.cut.checkpoint, report.cut.components), self.owner,
                                                             _preflight=cap_preflight)
                    if capital.replayed:
                        raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
                    self._check(report, epoch + 1, market)
                    if _allocation is not None and not capital.decision.allowed:
                        return HedgeSourceReceipt(capital, None, market)
                    current = (await self.financial.observation_cut(self.binding)).cut
                    if current.checkpoint.revision != report.cut.checkpoint.revision + 1:
                        raise StorageError('PG_HEDGE_SOURCE_NATIVE_CHANGED')
                    self._capture = ('carry', report, epoch, snapshot, bundle, market)
                    carried = await self.carry.observe(carry_id, cut=ProductCut(current.checkpoint, current.components), owner=self.owner,
                                                       _preflight=carry_preflight)
                    if carried.replayed:
                        raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
                    self._check(report, epoch + 2, market)
                    current = (await self.financial.observation_cut(self.binding)).cut
                    if current.checkpoint.revision != report.cut.checkpoint.revision + 2:
                        raise StorageError('PG_HEDGE_SOURCE_NATIVE_CHANGED')
                    self._qualified = (report, epoch + 2, current, market, cp['leverage'], swap_policy['controller_id'])
                    return HedgeSourceReceipt(capital, carried, market)
        except BaseException as error:
            self.stop()
            if isinstance(error, (StorageError, asyncio.CancelledError, TimeoutError)):
                raise
            raise StorageError('PG_HEDGE_SOURCE_INVALID') from None
        finally:
            self._capture, self._busy = None, False

    def _allocation_task(self):
        if self._allocating and (not self._planning or self._plan_task is not asyncio.current_task()):
            raise StorageError('PG_SPOT_ALLOCATION_ORIGINAL_TASK_REQUIRED')

    async def allocate_spot(self, operation_id, intent):
        """Allocate once while both makers are paused and fully reconciled.

        Original signed observations finance this exact business intent before
        its NEW runner capture. Native denials and unknown COMMITs retain claims;
        this method never prepares an exchange order or grants HTTP permission.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
        identifier(operation_id)
        if (type(intent) is not PgSpotQuoteIntent or intent.owner != self.owner
                or intent.controller_id != self.reader.source.journal.controller_id
                or intent.config_version != self.reader.source.journal.config_version):
            raise StorageError('PG_SPOT_ALLOCATION_INTENT_INVALID')
        if self._planning or self._busy:
            raise StorageError('PG_HEDGE_SOURCE_BACKPRESSURE')
        self._planning, self._allocating, self._plan_task = True, True, asyncio.current_task()
        self._allocation_basis, self._qualified = None, None
        # Reusing the operation with a different intent must not manufacture
        # a fresh operation/observation cycle under another derived identity.
        key = fingerprint([operation_id])
        capital_id, carry_id = 'spot-capital:' + key, 'spot-carry:' + key
        try:
            self._original()
            async with asyncio.timeout(self.timeout_ms / 1000):
                cap_preflight = await preflight_new_operation(self.capital, capital_id)
                carry_preflight = await preflight_new_operation(self.carry, carry_id)
                if cap_preflight is None or carry_preflight is None:
                    raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
                request = _SpotAllocation('spot-source:' + key, intent, capital_id, carry_id, cap_preflight, carry_preflight)
                self._allocation_request = request
                observed = await self.refresh(request.source_operation, _allocation=request)
                return SpotAllocationReceipt(observed, observed.capital, observed.carry)
        except BaseException:
            self.stop()
            raise
        finally:
            self._capture, self._qualified, self._allocation_basis = None, None, None
            self._allocation_request = None
            self._planning, self._allocating, self._plan_task = False, False, None

    async def collect(self, operation_id, *, source_report=None):
        """Build one child from the native decision, with permanent identities.

        A diagnostic preflight is persisted first; SQL recomputes the decision
        when the actual child is attached. Interrupted preflights require explicit
        reconciliation and cannot recollect/retry under the original identity.
        """
        from hummingbot.core.data_type.common import PositionAction, TradeType
        from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
        from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
        identifier(operation_id)
        if self._planning or self._busy:
            raise StorageError('PG_HEDGE_SOURCE_BACKPRESSURE')
        self._planning = True
        self._plan_task = asyncio.current_task()
        try:
            self._original()
            repository = PgHedgeRepository(self.collector.swap)
            preflight = 'hedge-source-plan:' + fingerprint([operation_id])
            if await repository.lookup(preflight, self.binding) is not None:
                raise StorageError('PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION')
            result = await self.refresh(operation_id, source_report=source_report)
            report, epoch, cut, market, leverage, controller_id = self._qualified
            self._check(report, epoch, market)
            if not result.capital.decision.allowed or not result.carry.decision.allowed:
                return PlanHedge.capture(operation_id, market)
            decision = await repository.plan(PlanHedge.capture(preflight, market), cut=cut, owner=self.owner)
            self._check(report, epoch + 1, market)
            # No child is proposed for deadband, limits, missing scope or cost.
            if decision.decision['reason_code'] not in ('HEDGE_URGENT_BOUNDED_MAKER', 'HEDGE_ACTION_READY'):
                return PlanHedge.capture(operation_id, market)
            current = (await self.financial.observation_cut(self.binding)).cut
            self._check(report, epoch + 1, market)
            if current.checkpoint.revision != cut.checkpoint.revision + 1:
                raise StorageError('PG_HEDGE_SOURCE_NATIVE_CHANGED')
            key = fingerprint([operation_id])
            wire, intent = 'H' + key[:31], 'hedge:' + key
            # This is the action capture time, not a renewed feed timestamp.
            # Market, account and carry retain their original stamps and SQL
            # independently checks them again at prepare and final ARM.
            cfg = OrderExecutorConfig(id=intent, timestamp=utc(self.reader.source.journal.clock()).timestamp(),
                                      controller_id=controller_id, connector_name='okx_perpetual',
                                      trading_pair='LIFE-USDT', side=TradeType[decision.decision['side']],
                                      position_action=PositionAction[decision.decision['position_action']],
                                      amount=D(decision.decision['quantity_base']), price=D(decision.decision['price_usdt']),
                                      leverage=leverage, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
            claim = CapitalClaim(intent, wire, self.binding.account_uid, str(self.owner.session_id), self.owner.epoch,
                                 report.config_version, report.risk_epoch, 'SWAP',
                                 cfg.side.name, cfg.position_action.name, cfg.amount, cfg.price, cfg.leverage)
            child = HedgeChild('hedge-capital:' + key, 'hedge-carry:' + key,
                               PrepareSwap.from_config('hedge-prepare:' + key, wire, cfg), claim)
            return PlanHedge.capture(operation_id, market, child=child)
        except BaseException:
            self.stop()
            raise
        finally:
            self._planning, self._plan_task = False, None

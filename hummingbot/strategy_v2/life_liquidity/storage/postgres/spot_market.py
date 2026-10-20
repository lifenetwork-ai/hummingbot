"""Signed LIFE spot book/terms joined to the original native account report.

This is market provenance, not controller installation or a send capability.
Independent depth excludes reconciled own orders. Size-specific unwind values
consume that depth; a mid price or benchmark is never an executable exit value.
"""
import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal as D, localcontext

from hummingbot.core.data_type.common import OrderType
from hummingbot.strategy_v2.life_liquidity.fees import FeeRateSnapshot
from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules, MarketSnapshot
from hummingbot.strategy_v2.life_liquidity.own_depth import OwnBookOrder, separate_own_depth
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceDecision, ReferenceEngine
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import (
    PrepareQuoteAction,
    QuoteAdmissionFacts,
    QuotePolicySnapshot,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot, decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import _amount
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import JointSourceReport
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota

ROUTES = (
    ('/api/v5/public/instruments', {'instType': 'SPOT', 'instId': 'LIFE-USDT'}),
    ('/api/v5/market/books', {'instId': 'LIFE-USDT', 'sz': '64'}),
    ('/api/v5/account/trade-fee', {'instType': 'SPOT', 'instId': 'LIFE-USDT'}),
)


@dataclass(frozen=True)
class SpotMarketCapture:
    report: JointSourceReport
    received_at: datetime
    book: MarketSnapshot
    rules: InstrumentRules
    fees: FeeRateSnapshot
    reference: ReferenceDecision
    independent_bids: tuple[tuple[D, D], ...]
    independent_asks: tuple[tuple[D, D], ...]
    trading_permission: bool = field(default=False, init=False)


class PgSpotMarketSource:
    def __init__(self, hedge_source, *, reference_engine, max_observation_skew_ms,
                 max_depth_distance_bps, max_levels, timeout_ms):
        if (type(hedge_source) is not PgHedgeSource or type(reference_engine) is not ReferenceEngine
                or reference_engine.life_source_id != 'okx:LIFE-USDT'
                or type(max_observation_skew_ms) is not int or not 0 < max_observation_skew_ms <= 60000
                or type(max_depth_distance_bps) is not D or not max_depth_distance_bps.is_finite()
                or not 0 <= max_depth_distance_bps < 10000
                or type(max_levels) is not int or not 0 < max_levels <= 64
                or type(timeout_ms) is not int or not 0 < timeout_ms <= 60000):
            raise StorageError('PG_SPOT_MARKET_CONFIG_INVALID')
        self.source, self.engine = hedge_source, reference_engine
        self.collector, self.reader = hedge_source.collector, hedge_source.reader
        self.repository, self.runtime, self.binding = hedge_source.financial, hedge_source.runtime, hedge_source.binding
        self.max_skew_ms, self.max_distance_bps = max_observation_skew_ms, max_depth_distance_bps
        self.max_levels, self.timeout_ms = max_levels, timeout_ms
        self._swap_sdk, self._swap_auth = hedge_source.swap_source._connector, hedge_source.swap_source._connector._auth
        self._engine_hash = self._reference_hash()
        self._context = (hedge_source, reference_engine, self.collector, self.reader, self.repository, self.runtime,
                         self.binding, self.max_skew_ms, self.max_distance_bps, self.max_levels, self.timeout_ms)
        self._busy = self._stopped = False
        self._policy, self._capture, self._epoch = None, None, None
        self._bound_policy = None
        self._pending_quote = None
        self._preparing_quote, self._collection_task = None, None
        self._prepared_quote = None

    def owns_preparation_observation(self, application):
        """An observation may inspect its runner; it cannot admit a write."""
        self._original()
        return (self._busy and self._preparing_quote is application and self.reader._active
                and self._collection_task is asyncio.current_task() and self.runtime._observation_inflight == 1
                and self.runtime._healthy and not self.runtime._closing)

    @classmethod
    async def create(cls, hedge_source, **kwargs):
        self = cls(hedge_source, **kwargs)
        self._policy = (await self._policy_snapshot()).policy
        self._bound_policy = self._policy
        return self

    def _reference_hash(self):
        e = self.engine
        return fingerprint(dict(policy={k: str(v) if type(v) is D else v for k, v in e.policy.__dict__.items()}, life_anchor_usdt=str(e.life_anchor_usdt),
                                life_source_id=e.life_source_id, model_version=e.model_version,
                                source_anchors_usdt={k: str(v) for k, v in e.source_anchors_usdt.items()}))

    def _original(self):
        if (self._stopped or self._context != (
                self.source, self.engine, self.collector, self.reader, self.repository, self.runtime,
                self.binding, self.max_skew_ms, self.max_distance_bps, self.max_levels, self.timeout_ms)
                or self._reference_hash() != self._engine_hash or self._policy is not self._bound_policy):
            raise StorageError('PG_SPOT_MARKET_CONTEXT_CHANGED')
        self.source.assert_binding()
        swap = self.source.swap_source
        swap.boundary._original()
        if (swap._connector is not self._swap_sdk or self._swap_sdk._auth is not self._swap_auth
                or swap._credentials_values() != self.reader.source._values
                or (self._swap_auth._api_key, self._swap_auth._api_secret, self._swap_auth._passphrase) != swap._values
                or swap._connector.domain != swap._domain or swap._revoked
                or swap.boundary.runtime is not self.runtime or swap._runtime is not self.runtime
                or swap.boundary.binding != self.binding or swap.boundary.owner != self.source.owner
                or PgSharedSdkQuota.for_connector(self._swap_sdk) is not swap._quota
                or swap._order_url != self.reader.source.endpoint_url):
            raise StorageError('PG_SPOT_MARKET_SDK_CONTEXT_CHANGED')
        # Do not call swap._ready(): its final send fence deliberately requires
        # paused makers. Reading quote data neither pauses them nor arms a SWAP.
        if (self.source.reader is not self.reader or self.source.collector is not self.collector
                or self.collector.reader is not self.reader or self.reader.repository is not self.repository
                or self.source.financial is not self.repository or self.source.runtime is not self.runtime
                or self.runtime._closing):
            raise StorageError('PG_SPOT_MARKET_CONTEXT_CHANGED')

    def _now(self):
        return utc(self.reader.source.journal.clock())

    async def _policy_snapshot(self):
        self._original()
        before, started = self._now(), time.monotonic()
        value = await self.repository.quote_admission_policy(self.binding)
        after, elapsed = self._now(), time.monotonic() - started
        # DB and SDK wall clocks need not have zero offset. Use the existing
        # configured clock-skew bound, as the quote identity source does;
        # exact microsecond containment also rejects harmless stamp rounding.
        skew_ms = self.reader.source.max_clock_skew_ms
        skew = timedelta(milliseconds=skew_ms)
        if (type(value) is not QuotePolicySnapshot or value.policy.binding != self.binding
                or after < before or not before - skew <= value.observed_at <= after + skew
                or abs((after - before).total_seconds() - elapsed) * 1000 > skew_ms):
            raise StorageError('PG_SPOT_MARKET_POLICY_UNQUALIFIED')
        self._original()
        return value

    def stop(self):
        self._stopped, self._capture = True, None
        self._prepared_quote = None
        self._context[5]._healthy = False

    @staticmethod
    def _one(value):
        if type(value) is not list or len(value) != 1 or type(value[0]) is not dict:
            raise StorageError('PG_SPOT_MARKET_RESPONSE_SCOPE')
        return value[0]

    def _sdk_scope(self, report, *, pending_quote=None, armed=False):
        """Exact original active SDK orders, not a complete runner topology proof."""
        expected = {'SPOT': {}, 'SWAP': {}}
        own = []
        pending = None
        if pending_quote is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_transport import PgQuoteRequestGuard
            if (type(pending_quote) is not PgQuoteRequestGuard
                    or pending_quote._repository is not self.repository
                    or pending_quote._journal is not self.reader.source.journal
                    or pending_quote._account_source is None
                    or pending_quote._account_source._market_source is not self):
                raise StorageError('PG_SPOT_MARKET_PENDING_CONTEXT')
            pending = pending_quote.pending_order(armed=armed)
            if pending_quote._action.executor_config.id not in report.runner_executor_ids:
                raise StorageError('PG_SPOT_MARKET_PENDING_RUNNER_MISSING')
        pending_seen = False
        for evidence in report.orders:
            native = json.loads(evidence.native_json)
            order = None if evidence.exchange_json is None else json.loads(evidence.exchange_json)
            if native['state'] == 'ABORTED_BEFORE_SEND' and order is None:
                continue
            if pending is not None and native['wire_id'] == pending.client_order_id:
                if order is not None or evidence.trade_ids or json.loads(evidence.fills_json):
                    raise StorageError('PG_SPOT_MARKET_PENDING_CONTEXT')
                pending_quote.pending_order(native, armed=armed)
                pending_seen = True
                continue  # This unsent order cannot contribute public depth.
            if order is None or native['state'] in ('PREPARED', 'SEND_UNKNOWN'):
                raise StorageError('PG_SPOT_MARKET_ORDER_UNRESOLVED')
            if order['state'] not in ('live', 'partially_filled'):
                continue
            expected[native['product']][native['wire_id']] = order, native
            if native['product'] == 'SPOT':
                own.append(OwnBookOrder(native['wire_id'], order['ordId'], order['instId'], native['side'],
                                        _amount(order['px']), _amount(order['sz']) - _amount(order['accFillSz']), 'OPEN'))
        for product, sdk in (('SPOT', self.reader.source._connector), ('SWAP', self.source.swap_source._connector)):
            active = sdk._order_tracker.active_orders
            pending_ids = {pending.client_order_id} if pending is not None and product == 'SPOT' else set()
            if set(active) != set(expected[product]) | pending_ids:
                raise StorageError('PG_SPOT_MARKET_SDK_SCOPE')
            for wire, order in active.items():
                if wire in pending_ids:
                    continue
                fact, native = expected[product][wire]
                scale = D('1') if product == 'SPOT' else sdk._contract_sizes.get('LIFE-USDT')
                if (scale is None or order.client_order_id != wire or order.exchange_order_id != fact['ordId']
                        or order.trading_pair != 'LIFE-USDT' or order.order_type is not OrderType.LIMIT_MAKER
                        or order.trade_type.name.lower() != fact['side'] or order.price != _amount(fact['px'])
                        or order.amount != _amount(fact['sz']) * scale
                        or order.executed_amount_base != _amount(fact['accFillSz']) * scale
                        or product == 'SWAP' and (order.leverage != int(fact['lever'])
                                                  or order.position.name != native['position_action'])):
                    raise StorageError('PG_SPOT_MARKET_SDK_SCOPE')
        if pending is not None and not pending_seen:
            raise StorageError('PG_SPOT_MARKET_PENDING_CONTEXT')
        return tuple(own)

    def _rules_match(self, rules):
        p = self._policy
        sdk_rule = self.reader.source._connector.trading_rules.get('LIFE-USDT')
        if ((rules.tick_size, rules.lot_size, rules.min_size) != (p.tick_size, p.lot_size, p.min_size)
                or sdk_rule is None or (sdk_rule.min_price_increment, sdk_rule.min_base_amount_increment,
                                        sdk_rule.min_order_size) != (p.tick_size, p.lot_size, p.min_size)):
            raise StorageError('PG_SPOT_MARKET_RULES_MISMATCH')

    def _parse(self, report, received):
        data = json.loads(report.spot_market_json)
        if type(data) is not list or len(data) != 3:
            raise StorageError('PG_SPOT_MARKET_RESPONSE_SCOPE')
        instrument, row, terms = (self._one(v) for v in data)
        if any(instrument.get(k) != v for k, v in dict(
                instType='SPOT', instId='LIFE-USDT', baseCcy='LIFE', quoteCcy='USDT', state='live').items()):
            raise StorageError('PG_SPOT_MARKET_INSTRUMENT_SCOPE')
        rules = InstrumentRules.from_okx(instrument)
        p = self._policy
        self._rules_match(rules)
        fees = FeeRateSnapshot.from_okx(dict(code='0', data=[terms]), account_id=self.binding.account_uid,
                                        connector_name='okx', instrument=instrument, notional_currency='USDT')
        if (len(terms['feeGroup']) > 64 or any(type(g) is not dict for g in terms['feeGroup'])
                or len({g.get('groupId') for g in terms['feeGroup']}) != len(terms['feeGroup'])
                or max(abs(fees.maker_cost_rate), abs(fees.taker_cost_rate)) > 1):
            raise StorageError('PG_SPOT_MARKET_FEE_INVALID')
        now_ms = int(received.timestamp() * 1000)
        for name in ('bids', 'asks'):
            if type(row.get(name)) is not list or not 0 < len(row[name]) <= 64:
                raise StorageError('PG_SPOT_MARKET_DEPTH_INVALID')
            for level in row[name]:
                if type(level) is not list or len(level) != 4:
                    raise StorageError('PG_SPOT_MARKET_DEPTH_INVALID')
                price, size = (_amount(v) for v in level[:2])
                if min(price, size) <= 0 or price % p.tick_size or size % p.lot_size:
                    raise StorageError('PG_SPOT_MARKET_DEPTH_INVALID')
        book = MarketSnapshot.from_okx(dict(code='0', data=[row]), 'LIFE-USDT', report.spot_market_monotonic,
                                       'live', now_ms - int(row['ts']))
        if (not fees.is_fresh(exchange_now_ms=now_ms, max_age_ms=p.max_fee_age_ms)
                or not 0 <= book.observed_age_ms <= min(p.max_fact_age_ms, p.max_reference_age_ms, self.engine.policy.max_age_ms)
                or max(book.exchange_timestamp_ms, fees.exchange_timestamp_ms) - min(
                    book.exchange_timestamp_ms, fees.exchange_timestamp_ms) > self.max_skew_ms):
            raise StorageError('PG_SPOT_MARKET_STALE')
        own = self._sdk_scope(report, pending_quote=self._pending_quote)
        decision = separate_own_depth(book=book, own_orders=own, order_scope_complete=True,
                                      orders_observed_monotonic=report.account_observed_monotonic,
                                      max_observation_skew_ms=self.max_skew_ms, max_book_age_ms=p.max_fact_age_ms,
                                      max_depth_distance_bps=self.max_distance_bps)
        if decision.evidence is None:
            raise StorageError('PG_SPOT_MARKET_' + decision.reason_code)
        reference = self.engine.evaluate('market', market=decision.evidence, exchange_now_ms=now_ms)
        if reference.price_usdt is None or reference.quality != 'QUALIFIED':
            raise StorageError('PG_SPOT_MARKET_' + reference.reason_code)
        levels = {'BUY': dict(book.bid_depth), 'SELL': dict(book.ask_depth)}
        for order in own:
            levels[order.side][order.price_usdt] -= order.remaining_base
        bids = tuple((price, levels['BUY'][price]) for price, _ in book.bid_depth if levels['BUY'][price] > 0)
        asks = tuple((price, levels['SELL'][price]) for price, _ in book.ask_depth if levels['SELL'][price] > 0)
        return SpotMarketCapture(report, received, book, rules, fees, reference, bids[:self.max_levels], asks[:self.max_levels])

    async def collect(self, *, pending_quote=None, preparing_quote=None):
        if pending_quote is not None and preparing_quote is not None:
            raise StorageError('PG_SPOT_MARKET_PHASE_INVALID')
        if self._busy:
            raise StorageError('PG_SPOT_MARKET_BACKPRESSURE')
        self._busy, self._capture = True, None
        self._prepared_quote = None
        self._pending_quote = pending_quote
        self._preparing_quote, self._collection_task = preparing_quote, asyncio.current_task()
        try:
            async with asyncio.timeout(self.timeout_ms / 1000):
                self._original()
                report = await self.collector.collect(include_spot_market=True, preparing_quote=preparing_quote)
                received, epoch = self._now(), self.collector._epoch
                if report.quote_policy != self._policy:
                    raise StorageError('PG_SPOT_MARKET_POLICY_CHANGED')
                with localcontext() as precision:
                    precision.prec = 160
                    capture = self._parse(report, received)
                self._original()
                self.collector.check_report(report, epoch=epoch)
                self._capture, self._epoch = capture, epoch
                return capture
        except BaseException as error:
            self.stop()
            if isinstance(error, (StorageError, asyncio.CancelledError)):
                raise
            raise StorageError('PG_SPOT_MARKET_INVALID') from None
        finally:
            self._busy = False
            self._preparing_quote, self._collection_task = None, None

    async def prepare(self, application, wire, *, subsidy_quote_usdt, fee_buffer_quote_usdt,
                      fee_buffer_base_life, carry_cost_usdt, inventory_risk_usdt):
        """Read after original dispatch; build a DTO, never reserve/arm/send."""
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
        if (type(application) is not PgSpotQuoteApplication or application.source._market_source is not self
                or application.repository is not self.repository or application.journal is not self.reader.source.journal):
            raise StorageError('PG_SPOT_MARKET_PREPARING_CONTEXT')
        action = application.preparing_action()
        row = application.journal._entry(action).row
        if wire != application.source.wire_for_action(action):
            raise StorageError('PG_SPOT_MARKET_PREPARING_CONTEXT')
        # Validate explicit economic inputs before any provider work.
        subsidy, quote_fee, base_fee, carry, inventory = (
            decimal(v) for v in (subsidy_quote_usdt, fee_buffer_quote_usdt, fee_buffer_base_life,
                                 carry_cost_usdt, inventory_risk_usdt))
        capture = await self.collect(preparing_quote=application)
        if application.preparing_action() != action:
            raise StorageError('PG_SPOT_MARKET_PREPARING_CONTEXT')
        cfg = action.executor_config
        facts = self.admission(capture, side=cfg.side.name, quantity_base=cfg.amount,
                               carry_cost_usdt=carry, inventory_risk_usdt=inventory)
        prepare = PrepareSpot('quote-prepare:' + fingerprint(row.json()), cfg.id, wire, cfg.trading_pair, cfg.side.name,
                              cfg.amount, cfg.price, facts.reference_price_usdt, subsidy, row.session_id,
                              row.epoch, row.config_version, row.level, quote_fee, base_fee, capture.report.risk_epoch)
        command = PrepareQuoteAction(row, prepare, facts)
        self._prepared_quote = application, asyncio.current_task(), command, capture, self._epoch
        return command

    def take_preparation_cut(self, application, command):
        """Consume the owning source's original cut; SQL still verifies it."""
        self._original()
        sealed = self._prepared_quote
        if sealed is None:
            raise StorageError('PG_SPOT_MARKET_PREPARATION_ORIGIN_REQUIRED')
        app, task, original, capture, epoch = sealed
        if (application is not app or asyncio.current_task() is not task or command is not original
                or app.source._market_source is not self or app._pending.get(command.action.intent_id) is not task
                or app._stopped or capture is not self._capture or epoch != self._epoch):
            raise StorageError('PG_SPOT_MARKET_PREPARATION_ORIGIN_REQUIRED')
        app._original()
        self.collector.check_report(capture.report, epoch=epoch)
        self._prepared_quote = None
        return capture.report.cut

    def admission(self, capture, *, side, quantity_base, carry_cost_usdt, inventory_risk_usdt, pending_quote=None):
        """Size-aware independent unwind; monetary admission remains native SQL."""
        try:
            self._original()
            if (capture is not self._capture or type(capture) is not SpotMarketCapture or side not in ('BUY', 'SELL')
                    or pending_quote is not self._pending_quote):
                raise StorageError('PG_SPOT_MARKET_CAPTURE_CHANGED')
            self.collector.check_report(capture.report, epoch=self._epoch)
            self._rules_match(capture.rules)
            p = self._policy
            now = int(self._now().timestamp() * 1000)
            if (not 0 <= now - int(capture.received_at.timestamp() * 1000) <= p.max_fact_age_ms
                    or not 0 <= now - capture.book.exchange_timestamp_ms <= min(p.max_reference_age_ms, self.engine.policy.max_age_ms)
                    or not capture.fees.is_fresh(exchange_now_ms=now, max_age_ms=p.max_fee_age_ms)):
                raise StorageError('PG_SPOT_MARKET_STALE')
            self._sdk_scope(capture.report, pending_quote=pending_quote)
            with localcontext() as precision:
                precision.prec = 160
                quantity = decimal(quantity_base, positive=True)
                carry, inventory = decimal(carry_cost_usdt), decimal(inventory_risk_usdt)
                remaining, value = quantity, D('0')
                for price, size in capture.independent_bids if side == 'BUY' else capture.independent_asks:
                    take = min(remaining, size)
                    value += price * take
                    remaining -= take
                    if remaining == 0:
                        break
                if remaining != 0:
                    raise StorageError('PG_SPOT_MARKET_EXIT_DEPTH_INSUFFICIENT')
                # Native amounts have at most 28 fractional digits. Rounding
                # must worsen the unwind, including recurring VWAP ratios.
                exit_value = (value / quantity).quantize(D('1e-28'), rounding=ROUND_FLOOR if side == 'BUY' else ROUND_CEILING)
                book_at = datetime.fromtimestamp(capture.book.exchange_timestamp_ms / 1000, timezone.utc)
                fee_at = datetime.fromtimestamp(capture.fees.exchange_timestamp_ms / 1000, timezone.utc)
                return QuoteAdmissionFacts(self.binding, capture.received_at, book_at, book_at, fee_at,
                                           capture.reference.price_usdt, decimal(exit_value, positive=True),
                                           capture.book.bid, capture.book.ask, capture.fees.conservative_cost_rate('maker'),
                                           capture.fees.conservative_cost_rate('taker'), D('0'), carry, inventory, True, 'independent_market')
        except StorageError as error:
            # Insufficient executable depth is an ordinary size rejection.
            # It must not poison a still-qualified smaller quote/other side.
            if error.code != 'PG_SPOT_MARKET_EXIT_DEPTH_INSUFFICIENT':
                self.stop()
            raise
        except BaseException:
            self.stop()
            raise

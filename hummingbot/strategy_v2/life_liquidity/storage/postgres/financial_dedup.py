"""Financially qualified reporting IDs for explicit SPOT SDK cache recovery.

Tracking JSON remains diagnostic. This restores duplicate-suppression IDs only,
never an SDK order tracker, order sender, owner, readiness or financial balance.
"""
import asyncio
import inspect
import json
from decimal import Decimal, localcontext

from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection


class PgSpotDedupRecovery:
    def __init__(self, reader, events, *, local_idle):
        if (type(reader) is not PgSpotAccountReader or type(events) is not PgEventRepository
                or events.financial is not reader.repository or events._runtime is not reader.runtime
                or not callable(local_idle) or inspect.iscoroutinefunction(local_idle)
                or events.source.market != 'okx' or events.source.controller_id != reader.source.journal.controller_id):
            raise StorageError('PG_SPOT_DEDUP_CONTEXT_INVALID')
        self.reader, self.events, self.runtime = reader, events, reader.runtime
        self.local_idle = local_idle
        self.context = PgSpotObservationReader(reader.repository, max_rows=events.max_rows, max_bytes=events.max_read_bytes)
        if not hasattr(self.runtime, '_dedup_recovery_mutex'):
            self.runtime._dedup_recovery_mutex = asyncio.Lock()
        self._lock = self.runtime._dedup_recovery_mutex

    @staticmethod
    def _fee(body, fill):
        try:
            fee = json.loads(body['fee_json'])
            # The actual OKX parser represents signed native fees as one flat
            # token amount. Percent fees or other currencies are not inferred.
            expected = -fill.signed_fee
            if (Decimal(str(fee['percent'])) != 0 or fee.get('percent_token') != fill.fee_currency
                    or type(fee.get('flat_fees')) is not list or len(fee['flat_fees']) != 1
                    or fee['flat_fees'][0].get('token') != fill.fee_currency
                    or Decimal(str(fee['flat_fees'][0]['amount'])) != expected
                    or Decimal(body['fee_quote']) != expected * (fill.price_usdt if fill.fee_currency == 'LIFE' else Decimal('1'))):
                raise StorageError('PG_SPOT_DEDUP_FEE_UNQUALIFIED')
        except (KeyError, TypeError, ValueError, ArithmeticError):
            raise StorageError('PG_SPOT_DEDUP_FEE_UNQUALIFIED') from None

    async def restore(self):
        if self._lock.locked():
            raise StorageError('PG_SPOT_DEDUP_BACKPRESSURE')
        async with self._lock:
            self.runtime._observation_inflight += 1
            self.runtime._ingress_epoch += 1
            try:
                with localcontext() as precision:
                    precision.prec = 160
                    async with self.reader.collection():
                        context = await self.context.observation_context(self.reader.binding)
                        if context.pending:
                            raise StorageError('PG_SPOT_DEDUP_FINANCIAL_PENDING')
                        rows, cut = await self.events._read(self.reader.binding, False, with_cut=True)
                        if cut != context.cut:
                            raise StorageError('PG_SPOT_DEDUP_CUT_CHANGED')
                        commands = {row.client_order_id: row for row in context.commands}
                        fills = {row.fill.trade_id: row for row in context.fills}
                        attempts = {}
                        trades, exchanges = set(), {}
                        for row in rows:
                            command = EventProjection.from_json(row['body']['command'])
                            if command.operation_id != row['projection_id']:
                                raise StorageError('PG_SPOT_DEDUP_EVENT_IDENTITY_UNQUALIFIED')
                            if command.source != self.events.source:
                                continue
                            body = json.loads(command.payload_json)
                            if body['kind'] not in ('BuyOrderCreated', 'SellOrderCreated', 'OrderFilled'):
                                continue
                            prepared = commands.get(body['wire'])
                            if prepared is None or body['pair'] != 'LIFE-USDT' or body['side'] != prepared.side:
                                raise StorageError('PG_SPOT_DEDUP_ORIGIN_UNQUALIFIED')
                            if body['wire'] not in attempts:
                                attempts[body['wire']] = await self.reader.repository.attempt(prepared.intent_id, self.reader.binding)
                            attempt = attempts[body['wire']]
                            if (attempt is None or attempt.exchange_order_id is None
                                    or attempt.state.value not in ('ACKED', 'TERMINAL')
                                    or body['exchange_id'] != attempt.exchange_order_id
                                    or body['order_type'] != 'LIMIT_MAKER' or body['leverage'] != 1
                                    or body['position'] not in ('NIL', 'OPEN')):
                                raise StorageError('PG_SPOT_DEDUP_ACK_UNQUALIFIED')
                            if body['kind'] == 'OrderFilled':
                                native = fills.get(body['trade_id'])
                                if (native is None or native.intent_id != prepared.intent_id
                                        or native.prepare_operation_id != prepared.operation_id
                                        or native.exchange_order_id != body['exchange_id']
                                        or (Decimal(body['amount']), Decimal(body['price'])) != (
                                            native.fill.quantity_base_life, native.fill.price_usdt)):
                                    raise StorageError('PG_SPOT_DEDUP_FILL_UNQUALIFIED')
                                self._fee(body, native.fill)
                                trades.add(TradeFillOrderDetails('okx', body['trade_id'], 'LIFE-USDT'))
                            elif (Decimal(body['amount']), Decimal(body['price'])) != (prepared.quantity_base, prepared.limit_price_usdt):
                                raise StorageError('PG_SPOT_DEDUP_ORDER_UNQUALIFIED')
                            if body['exchange_id'] in exchanges and exchanges[body['exchange_id']] != body['wire']:
                                raise StorageError('PG_SPOT_DEDUP_EXCHANGE_ID_CONFLICT')
                            exchanges[body['exchange_id']] = body['wire']
                    # The final UID await cannot adopt a replacement repository,
                    # credential or reporting/financial cut.
                    if (await self.reader.repository.snapshot(self.reader.binding)).cut != context.cut:
                        raise StorageError('PG_SPOT_DEDUP_CUT_CHANGED')
                    self.reader.source._credentials()
                    if (self.reader.source.journal.repository is not self.reader.repository or self.runtime._closing
                            or self.reader.repository._runtime is not self.runtime
                            or self.reader.source.journal.owner != self.reader.owner
                            or self.reader.source.journal.binding != self.reader.binding
                            or self.local_idle() is not True):
                        raise StorageError('PG_SPOT_DEDUP_CONTEXT_CHANGED')
                    connector = self.reader.source._connector
                    if (not connector._current_trade_fills.issubset(trades)
                            or any(exchanges.get(key) != value for key, value in connector._exchange_order_ids.items())):
                        raise StorageError('PG_SPOT_DEDUP_EXISTING_CACHE_UNQUALIFIED')
                    old_trades, old_exchanges = set(connector._current_trade_fills), dict(connector._exchange_order_ids)
                    try:
                        connector.add_trade_fills_from_market_recorder(trades)
                        connector.add_exchange_order_ids_from_market_recorder(exchanges)
                        if connector._current_trade_fills != trades or connector._exchange_order_ids != exchanges:
                            raise StorageError('PG_SPOT_DEDUP_PUBLICATION_INCOMPLETE')
                    except BaseException:
                        connector._current_trade_fills.clear()
                        connector._current_trade_fills.update(old_trades)
                        connector._exchange_order_ids.clear()
                        connector._exchange_order_ids.update(old_exchanges)
                        raise
                    return len(trades), len(exchanges)
            except BaseException as error:
                self.runtime._healthy = False
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageError('PG_SPOT_DEDUP_UNAVAILABLE') from None
            finally:
                self.runtime._observation_inflight -= 1

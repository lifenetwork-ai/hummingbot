"""Joint SDK duplicate IDs from original native facts and a fresh signed proof.

Both product caches publish synchronously or both roll back. Diagnostic tracking
JSON, callback amounts and reconstructed source reports confer no authority.
"""
import asyncio
import inspect
import json
from decimal import Decimal as D, localcontext

from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_dedup import PgSpotDedupRecovery
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import SwapFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection


class PgJointDedupRecovery:
    def __init__(self, observer, events, *, local_idle):
        if (type(observer) is not PgJointFinancialObserver or type(events) is not tuple or len(events) != 2
                or any(type(r) is not PgEventRepository or r.financial is not observer.financial
                       or r._runtime is not observer.runtime
                       or r.source.controller_id != observer.reader.source.journal.controller_id for r in events)
                or tuple(r.source.market for r in events) != ('okx', 'okx_perpetual')
                or not callable(local_idle) or inspect.iscoroutinefunction(local_idle)):
            raise StorageError('PG_JOINT_DEDUP_CONTEXT_INVALID')
        self.observer, self.events, self.local_idle = observer, events, local_idle
        self.collector, self.runtime = observer.collector, observer.runtime
        self.financial, self.binding, self.reader = observer.financial, observer.binding, observer.reader
        self.connectors = (self.reader.source._connector, observer.source.swap_source._connector)
        self._context = (observer, events, local_idle, self.collector, self.runtime, self.financial, self.binding, self.reader, self.connectors)
        self._sources = tuple(r.source for r in events)
        self._auth = tuple(c._auth for c in self.connectors)
        if not hasattr(self.runtime, '_dedup_recovery_mutex'):
            self.runtime._dedup_recovery_mutex = asyncio.Lock()
        self._lock = self.runtime._dedup_recovery_mutex

    def _original(self):
        self.observer._original()
        swap = self.observer.source.swap_source
        if (self._context != (self.observer, self.events, self.local_idle, self.collector, self.runtime,
                              self.financial, self.binding, self.reader, self.connectors)
                or self.connectors != (self.reader.source._connector, swap._connector)
                or tuple(c._auth for c in self.connectors) != self._auth
                or swap._credentials_values() != swap._values or swap._connector.domain != swap._domain
                or swap._values != self.reader.source._values or swap._order_url != self.reader.source.endpoint_url
                or (swap._connector._auth._api_key, swap._connector._auth._api_secret,
                    swap._connector._auth._passphrase) != swap._values
                or swap.boundary.swap is not self.collector.swap or swap._runtime is not self.runtime
                or swap._binding != self.binding or swap._owner != self.observer.owner
                or tuple(r.source for r in self.events) != self._sources
                or any(r.financial is not self.financial or r._runtime is not self.runtime for r in self.events)
                or self.runtime._closing):
            raise StorageError('PG_JOINT_DEDUP_CONTEXT_CHANGED')

    def _history(self, rows, source, report, product):
        orders = {}
        for evidence in report.orders:
            native = json.loads(evidence.native_json)
            if native['product'] != product:
                continue
            exchange = None if evidence.exchange_json is None else json.loads(evidence.exchange_json)
            kind = SpotFillObservation if product == 'SPOT' else SwapFillObservation
            fills = {f.trade_id: f for f in map(kind.from_json, json.loads(evidence.fills_json))}
            orders[native['wire_id']] = native, exchange, fills
        trades, exchanges = set(), {}
        for row in rows:
            command = EventProjection.from_json(row['body']['command'])
            if command.operation_id != row['projection_id']:
                raise StorageError('PG_JOINT_DEDUP_EVENT_IDENTITY_UNQUALIFIED')
            if command.source != source:
                continue
            body = json.loads(command.payload_json)
            if body['kind'] not in ('BuyOrderCreated', 'SellOrderCreated', 'OrderFilled'):
                continue
            original = orders.get(body['wire'])
            if original is None:
                raise StorageError('PG_JOINT_DEDUP_ORIGIN_UNQUALIFIED')
            native, exchange, fills = original
            retired = (product == 'SPOT' and native.get('retired_spot') is True
                       and native['state'] == 'TERMINAL' and exchange is None)
            position = ('NIL', 'OPEN') if product == 'SPOT' else (native['position_action'],)
            leverage = D('1') if product == 'SPOT' else D(exchange['lever']) if exchange is not None else None
            if (exchange is None and not retired or native['state'] not in ('ACKED', 'TERMINAL')
                    or native['exchange_order_id'] != body['exchange_id']
                    or not retired and exchange['ordId'] != body['exchange_id']
                    or body['pair'] != 'LIFE-USDT' or body['side'] != native['side']
                    or body['order_type'] != 'LIMIT_MAKER' or body['position'] not in position
                    or body['leverage'] != leverage):
                raise StorageError('PG_JOINT_DEDUP_ACK_UNQUALIFIED')
            if body['kind'] == 'OrderFilled':
                fill = fills.get(body['trade_id'])
                if (fill is None or (D(body['amount']), D(body['price'])) != (fill.quantity_base_life, fill.price_usdt)):
                    raise StorageError('PG_JOINT_DEDUP_FILL_UNQUALIFIED')
                PgSpotDedupRecovery._fee(body, fill)
                trades.add(TradeFillOrderDetails(source.market, fill.trade_id, 'LIFE-USDT'))
            elif (D(body['amount']), D(body['price'])) != (D(native['quantity']), D(native['price'])):
                raise StorageError('PG_JOINT_DEDUP_ORDER_UNQUALIFIED')
            if body['exchange_id'] in exchanges and exchanges[body['exchange_id']] != body['wire']:
                raise StorageError('PG_JOINT_DEDUP_EXCHANGE_ID_CONFLICT')
            exchanges[body['exchange_id']] = body['wire']
        return trades, exchanges

    async def restore(self):
        if self._lock.locked():
            raise StorageError('PG_JOINT_DEDUP_BACKPRESSURE')
        async with self._lock:
            self.runtime._observation_inflight += 1
            try:
                self._original()
                with localcontext() as precision:
                    precision.prec = 160
                    original_epoch = self.runtime._ingress_epoch
                    original_rows = []
                    for repository in self.events:
                        rows, cut = await repository._read(self.binding, False, with_cut=True)
                        original_rows.append((rows, cut))
                    # History reads grant no source authority. Finish them
                    # before a NEW authenticated collection, while retaining
                    # both original cuts and the operation's original epoch.
                    if self.runtime._ingress_epoch != original_epoch:
                        raise StorageError('PG_JOINT_DEDUP_INGRESS_CHANGED')
                    report = await self.collector.collect()
                    epoch = self.collector._epoch
                    if epoch != original_epoch + 1:
                        raise StorageError('PG_JOINT_DEDUP_INGRESS_CHANGED')
                    histories = []
                    for repository, product, (rows, cut) in zip(self.events, ('SPOT', 'SWAP'), original_rows):
                        if cut != report.cut:
                            raise StorageError('PG_JOINT_DEDUP_CUT_CHANGED')
                        histories.append(self._history(rows, repository.source, report, product))
                    if (await self.financial.snapshot(self.binding)).cut != report.cut:
                        raise StorageError('PG_JOINT_DEDUP_CUT_CHANGED')
                    self._original()
                    self.collector.check_report(report, epoch=epoch)
                    if self.local_idle() is not True:
                        raise StorageError('PG_JOINT_DEDUP_IDLE_REQUIRED')
                    for connector, (trades, exchanges) in zip(self.connectors, histories):
                        if (not connector._current_trade_fills.issubset(trades)
                                or any(exchanges.get(k) != v for k, v in connector._exchange_order_ids.items())):
                            raise StorageError('PG_JOINT_DEDUP_EXISTING_CACHE_UNQUALIFIED')
                    previous = [(set(c._current_trade_fills), dict(c._exchange_order_ids)) for c in self.connectors]
                    try:
                        for connector, (trades, exchanges) in zip(self.connectors, histories):
                            connector.add_trade_fills_from_market_recorder(trades)
                            connector.add_exchange_order_ids_from_market_recorder(exchanges)
                            if connector._current_trade_fills != trades or connector._exchange_order_ids != exchanges:
                                raise StorageError('PG_JOINT_DEDUP_PUBLICATION_INCOMPLETE')
                        self._original()
                        self.collector.check_report(report, epoch=epoch)
                        if self.local_idle() is not True:
                            raise StorageError('PG_JOINT_DEDUP_IDLE_REQUIRED')
                    except BaseException:
                        for connector, (trades, exchanges) in zip(self.connectors, previous):
                            connector._current_trade_fills.clear()
                            connector._current_trade_fills.update(trades)
                            connector._exchange_order_ids.clear()
                            connector._exchange_order_ids.update(exchanges)
                        raise
                    return tuple((len(t), len(e)) for t, e in histories)
            except BaseException as error:
                self._context[4]._healthy = False
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageError('PG_JOINT_DEDUP_UNAVAILABLE') from None
            finally:
                self.runtime._observation_inflight -= 1

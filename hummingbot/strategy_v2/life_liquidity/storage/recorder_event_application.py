"""Main-loop connector capture and cache updates; SQL stays on the file worker."""
import json

from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageConflict, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import CREATES, EventProjection, EventSource


class RecorderEventApplication:
    def __init__(self, reporting, sources):
        self.reporting = reporting
        self.sources = tuple(sources)  # (actual connector identity, frozen source)

    @classmethod
    async def install(cls, reporting, connectors):
        recorder = reporting.repository._recorder
        connectors = tuple(connectors)
        if (not connectors or len({connector.name for connector in connectors}) != len(connectors)
                or recorder._market_data_collection_config.market_data_collection_enabled):
            raise StorageError('RECORDER_EVENT_INSTALL_INVALID')
        sources = []
        for connector in connectors:
            if connector.name not in ('okx', 'okx_perpetual') or connector.display_name != connector.name:
                raise StorageError('RECORDER_EVENT_CONNECTOR_INVALID')
            if await connector.get_account_uid() != reporting.binding.account_uid:
                reporting.invalidate()
                raise StorageError('RECORDER_EVENT_UID_MISMATCH')
            source = EventSource(reporting.repository.controller_id, recorder.config_file_path,
                                 recorder.strategy_name, connector.name)
            history = await reporting.repository.connector_history(source, reporting.binding)
            sources.append((connector, source, history))
        if reporting._closed or not reporting._healthy or reporting.store._closed or reporting.store._uncertain:
            raise StorageError('RECORDER_EVENT_INSTALL_REVOKED')
        app = cls(reporting, ((connector, source) for connector, source, _ in sources))
        # No partial installation before all source/history qualification passes.
        recorder.install_repository_event_sink(app, connectors)
        for connector, _, history in sources:
            connector.add_trade_fills_from_market_recorder({TradeFillOrderDetails(*row) for row in history.trade_ids})
            connector.add_exchange_order_ids_from_market_recorder(dict(history.exchange_ids))
        recorder.start()
        return app

    def capture(self, tag, connector, event):
        reporting = self.reporting
        try:
            source = next((source for actual, source in self.sources if connector is actual), None)
            if source is None:
                raise StorageError('RECORDER_EVENT_CONNECTOR_MISMATCH')
            captured = EventProjection.capture(reporting.binding, source, tag, event, connector.tracking_states)
        except Exception:
            reporting.invalidate()
            raise

        def committed(_receipt):
            # Only known order/exchange and trade cache entries are updated.
            # Financial fills/fees/funding still require account reconciliation.
            body = json.loads(captured.payload_json)
            if body['kind'] in CREATES and body['exchange_id'] is not None:
                connector.add_exchange_order_ids_from_market_recorder({body['exchange_id']: body['wire']})
            elif body['kind'] == 'OrderFilled':
                connector.add_trade_fills_from_market_recorder({TradeFillOrderDetails(source.market, body['trade_id'], body['pair'])})
        return reporting._enqueue(('event', captured.event_id, captured.payload_json), captured, self._update, committed)

    async def _update(self, captured, callbacks):
        reporting = self.reporting
        async with reporting._mutex:
            original = await reporting.repository.event_origin(captured.event_id, reporting.binding)
            if original is not None:
                if original.command.payload_json != captured.payload_json or original.command.source != captured.source:
                    raise StorageConflict('RECORDER_EVENT_OPERATION_CONFLICT')
                command, cut, owner = original.command, original.original_cut, original.owner
            else:
                command, cut, owner = captured, None, reporting.runtime.owner
            receipt = (await reporting.repository.project_event(command, cut=cut, owner=owner) if cut is not None else
                       await reporting.repository.publish_current('event', command, owner=owner))
            for callback in tuple(callbacks):
                callback(receipt)
            return receipt

    def stop(self):
        self.reporting.repository._recorder.stop()

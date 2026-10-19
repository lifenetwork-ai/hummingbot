"""Read-only view of original ACK publication; no exchange action or permission."""
import copy
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, PrepareSpot


def ack_view(resources, records, acknowledgements, *, observed_at):
    """Explain only original selected ACKING rows without publishing anything.

    This view is not native provenance. Admission still has to verify the
    independent original native ACK receipt, event and order at its import cut.
    """
    resources, records = copy.deepcopy(resources), copy.deepcopy(records)
    selected = {a.operation_id: a for a in acknowledgements}

    def refuse():
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_PENDING_ACK_REQUIRED')

    if len(selected) != len(acknowledgements):
        refuse()
    pending = []
    for key, row in resources.get('acknowledgements', {}).items():
        ack = AckSpot(**{**row['command'], 'observed_at': datetime.fromisoformat(row['command']['observed_at'])})
        if ack.observed_at > observed_at:
            refuse()
        if row['state'] == 'ACKED':
            continue
        prepared = resources['commands'].get(ack.prepare_operation_id)
        wal = records.get(ack.intent_id)
        if (row['state'] != 'ACKING' or selected.get(key) != ack or key != ack.operation_id
                or prepared is None or prepared['state'] != 'ACKING' or wal is None
                or row['owner'] != prepared['original_owner']
                or row['fingerprint'] != fingerprint({'command': ack.json(), 'owner': row['owner']})):
            refuse()
        request = PrepareSpot.from_json(prepared['command'])
        if ((ack.prepare_operation_id, ack.intent_id, ack.client_order_id, ack.market, ack.side, ack.session_id, ack.epoch)
                != (request.operation_id, request.intent_id, request.client_order_id, request.market, request.side, request.session_id, request.epoch)
                or not datetime.fromisoformat(prepared['prepared_at']) <= ack.observed_at <= observed_at
                or wal['state'] not in ('SEND_UNKNOWN', 'ACKED') or wal['exchange_terminal_observed']
                or wal['exchange_order_id'] not in (None, ack.exchange_order_id)
                or wal['state'] == 'ACKED' and wal['exchange_order_id'] != ack.exchange_order_id
                or (wal['client_order_id'], wal['reservation_id'], wal['session_id'], wal['epoch'], wal['slot_market'], wal['slot_side'], wal['slot_level'])
                != (request.client_order_id, request.intent_id, request.session_id, request.epoch, request.market, request.side, request.quote_level)
                or any(other['intent_id'] != ack.intent_id and other['exchange_order_id'] == ack.exchange_order_id for other in records.values())
                or any(value['intent_id'] == ack.intent_id and value['exchange_order_id'] != ack.exchange_order_id for value in resources.get('fills', {}).values())):
            refuse()
        prepared['state'], row['state'] = 'ACKED', 'ACKED'
        wal.update(state='ACKED', exchange_order_id=ack.exchange_order_id)
        pending.append(key)
    return resources, records, pending

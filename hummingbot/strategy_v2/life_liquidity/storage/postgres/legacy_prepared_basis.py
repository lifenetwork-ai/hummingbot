"""Admission-only original PREPARING receipt proof for coupled File recovery."""
import copy
from datetime import datetime
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint


def prepared_view(resources, wallet, subsidy, records, preparations, owner, binding, observed_at, *, batch_id=None, applied=False):
    resources = copy.deepcopy(resources)
    pending = []
    if len({p.operation_id for p in preparations}) != len(preparations):
        raise StorageError('PG_LEGACY_APPLYING_PREPARED_RECEIPT_BASIS_REQUIRED')
    for prepared in preparations:
        def refuse():
            raise StorageError('PG_LEGACY_APPLYING_PREPARED_RECEIPT_BASIS_REQUIRED')
        row = resources['commands'].get(prepared.operation_id)
        if (row is None or row['command'] != prepared.json() or row['original_owner'] != owner
                or row['fingerprint'] != fingerprint({'command': prepared.json(), 'owner': owner, 'cut': row['original_cut']})
                or row['original_cut']['checkpoint']['binding'] != binding
                or (prepared.session_id, prepared.epoch) != (owner['session_id'], owner['epoch'])):
            refuse()
        at = datetime.fromisoformat(row['prepared_at'])
        if not datetime.fromisoformat(owner['valid_from']) <= at < datetime.fromisoformat(owner['expires_at']) or at > observed_at:
            refuse()
        if row['state'] != 'PREPARING':
            if not applied or row.get('prepared_receipt_batch') != batch_id:
                refuse()
            continue
        if applied:
            refuse()
        held, cost, wal = wallet['reservations'].get(prepared.intent_id), subsidy['entries'].get(prepared.intent_id), records.get(prepared.intent_id)
        if (any(row.get(key) is not None for key in ('arm_owner', 'arm_cut', 'arm_fingerprint'))
                or wal is None or wal['state'] != 'PREPARED' or wal['exchange_order_id'] is not None
                or wal['exchange_terminal_observed'] or wal['cancel_requested'] or wal['cancel_attempts'] or wal['last_cancel_attempt_at'] is not None
                or (wal['client_order_id'], wal['session_id'], wal['epoch'], wal['reservation_id'], wal['slot_market'], wal['slot_side'], wal['slot_level'])
                != (prepared.client_order_id, prepared.session_id, prepared.epoch, prepared.intent_id, prepared.market, prepared.side, prepared.quote_level)
                or held is None or held['intent'] != dict(intent_id=prepared.intent_id, side=prepared.side,
                                                          quantity_base=str(prepared.quantity_base), limit_price_usdt=str(prepared.limit_price_usdt),
                                                          session_id=prepared.session_id, epoch=prepared.epoch) or held['state'] != 'OPEN' or D(held['filled_base']) != 0 or D(held['remaining_base']) != prepared.quantity_base
                or cost is None or cost['actual'] is not None or cost.get('inventory_cycle') is not None
                or D(cost.get('fill_floor', '0')) != 0 or D(cost['reserved']) != prepared.subsidy_quote_usdt
                or any(value['intent_id'] == prepared.intent_id for value in resources.get('fills', {}).values())):
            refuse()
        row['state'] = 'PREPARED'
        pending.append(prepared.operation_id)
    if any(row['state'] == 'PREPARING' for row in resources['commands'].values()):
        raise StorageError('PG_LEGACY_APPLYING_PREPARED_RECEIPT_BASIS_REQUIRED')
    return resources, pending

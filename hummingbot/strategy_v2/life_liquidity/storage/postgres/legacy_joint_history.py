"""Explain earlier complete File joint commands from original economic leaves.

No old wallet is an opening seed, and no native write or release occurs here.
Native prefix and source/approval checks remain independently mandatory.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_funding_history import (
    _after_money,
    _completed_acks,
    _wallet_money,
)


def completed_joint_history(source, batch, flows, policy, events, resources, *, writer, current_id=None):
    rows = resources.get('joint_cash_operations', {})

    def refuse():
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_JOINT_HISTORY_REQUIRED')

    if current_id is not None and current_id not in rows:
        refuse()
    bound = (FinancialCut.from_json(rows[current_id]['cut']).checkpoint.revision
             if current_id is not None else source.cut.checkpoint.revision)
    previous, revisions = [], set()
    for key, row in rows.items():
        old, cut, owner = JointCashSpot.from_json(row['command']), FinancialCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
        if (set(row) != {'command', 'cut', 'owner', 'fingerprint', 'state', 'before_native'}
                or key != old.operation_id or old.binding != source.cut.checkpoint.binding
                or cut.checkpoint.binding != old.binding or cut.checkpoint.revision >= source.cut.checkpoint.revision
                or owner.json() != writer or not owner.valid_from <= old.observed_at <= source.facts.observed_at
                or row['fingerprint'] != fingerprint({'command': old.json(), 'cut': cut.json(), 'owner': owner.json()})
                or {name: hashlib.sha256(bytes.fromhex(raw)).hexdigest() for name, raw in row['before_native'].items()} != dict(cut.components)
                or not old.order_scope_complete or not old.bill_scope_complete
                or any(events.get(event.event_id) != event for event in old.events)):
            refuse()
        if key == current_id:
            if row['state'] not in ('APPLYING', 'APPLIED'):
                refuse()
            continue
        if row['state'] != 'APPLIED' or cut.checkpoint.revision >= bound or cut.checkpoint.revision in revisions:
            refuse()
        revisions.add(cut.checkpoint.revision)
        _completed_acks(old, resources, row['owner'])
        # Only check original parent links here. The complete SPOT basis below
        # independently checks every preparation/ARM/hold; cloning its entire
        # current resource graph once per historical command is unnecessary.
        for prepared in old.spot_prepared_receipts:
            actual = resources.get('commands', {}).get(prepared.operation_id)
            if (actual is None or actual['command'] != prepared.json() or actual['original_owner'] != row['owner']
                    or actual['original_cut']['checkpoint']['binding'] != old.binding.json()
                    or actual.get('prepared_receipt_batch') != key or actual['state'] == 'PREPARING'):
                refuse()
            at = datetime.fromisoformat(actual['prepared_at'])
            if not owner.valid_from <= at < owner.expires_at or at > old.observed_at:
                refuse()
        for name, ids, complete in (('terminal_operations', old.spot_terminal_operations, 'TERMINAL'),
                                    ('settlement_operations', old.spot_settlement_operations, 'SETTLED')):
            for operation in ids:
                actual = resources.get(name, {}).get(operation)
                if actual is None or actual['state'] != complete or actual['command']['operation_id'] != operation:
                    refuse()
                facts = actual['command']['facts'] if name == 'terminal_operations' else actual['facts']
                if facts['binding'] != old.binding.json() or datetime.fromisoformat(facts['observed_at']) > old.observed_at:
                    refuse()
        previous.append((cut.checkpoint.revision, key, row, old))
    expected, earlier, prior_trades, prior_transfers, history = {}, {}, set(), set(), []
    opened = int(datetime.fromisoformat(policy['opened_at']).timestamp() * 1000)
    for _, key, row, old in sorted(previous):
        supplied = {event.event_id: event for event in old.events}
        if (not set(earlier) <= set(supplied)
                or any(not opened <= event.at_ms <= int(old.observed_at.timestamp() * 1000) for event in old.events)):
            refuse()
        wallet = json.loads(bytes.fromhex(row['before_native']['reservations']))
        base, cash, paid = _wallet_money(wallet, old, batch, flows, policy, events)
        if (paid != set(earlier) or not prior_trades <= set(wallet['trades'])
                or not prior_transfers <= {key.removeprefix('CASHFLOW:') for key in wallet['account_events'] if key.startswith('CASHFLOW:')}):
            refuse()
        base, cash, prior_trades, prior_transfers = _after_money(wallet, old, batch, flows, base, cash, resources, row['owner'])
        if (old.account_base_life, old.account_quote_usdt) != (
                base, cash + sum((event.signed_amount_usdt for event in old.events if event.event_id not in paid), D('0'))):
            refuse()
        for event in old.events:
            expected.setdefault(event.event_id, {'event': event.json(), 'operation_id': key})
        earlier = supplied
        history.append({'operation_id': key, 'content_hash': fingerprint(row)})
    if current_id is not None and rows[current_id]['state'] == 'APPLIED':
        current = JointCashSpot.from_json(rows[current_id]['command'])
        for event in current.events:
            expected.setdefault(event.event_id, {'event': event.json(), 'operation_id': current_id})
    if resources.get('joint_cashflows', {}) != expected:
        refuse()
    return earlier, {'joint_core_history': history} if history else {}

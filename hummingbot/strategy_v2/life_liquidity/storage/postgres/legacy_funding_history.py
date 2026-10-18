"""Replay completed original File financial commands before one APPLYING cut.

Read-only evidence replay. No wallet adoption, policy defaults or permissions.
Other historical command families still need their own original-basis proofs.
"""
import hashlib
import json
from datetime import datetime
from decimal import Decimal as D

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashSpot
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore


def _refuse():
    raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_BASIS_REQUIRED')


def _completed_acks(command, resources, owner):
    """Zero-money side effects still need their actual original receipts.

    The independent native ACK prefix proof checks native preparation/order,
    receipt and event provenance. This checks the original File parent/child
    relation; equal account totals cannot replace an omitted ACK receipt.
    """
    for ack in command.spot_acks:
        row = resources.get('acknowledgements', {}).get(ack.operation_id)
        prepared = resources.get('commands', {}).get(ack.prepare_operation_id)
        if (row is None or prepared is None or row['state'] != 'ACKED'
                or row['owner'] != owner or LegacySpotStore._ack_from_json(row['command']) != ack
                or row['fingerprint'] != fingerprint({'command': ack.json(), 'owner': owner})
                or ack.observed_at > command.observed_at
                or any(ack.json()[k] != prepared['command'][k] for k in (
                    'intent_id', 'client_order_id', 'market', 'side', 'session_id', 'epoch'))):
            _refuse()


def _wallet_money(wallet, command, batch, flows, policy, events, *, observed_at=None):
    """Explain each historical before-wallet from the independently seeded basis."""
    base, cash = D(policy['opening_base_life']), D(policy['opening_quote_usdt'])
    trades = {f.trade_id: (row, f) for row in batch.spot_fills for f in row.fills}
    observed_at = command.observed_at if observed_at is None else observed_at
    at = int(observed_at.timestamp() * 1000)
    fee_events = {}
    for trade_id, recorded in wallet['trades'].items():
        if trade_id not in trades:
            _refuse()
        row, fill = trades[trade_id]
        if (len(recorded) != 3 or (recorded[0], D(recorded[1]), D(recorded[2])) !=
                (row.intent_id, fill.quantity_base_life, fill.price_usdt)
                or max(fill.fill_at_ms, fill.independent_value_at_ms, fill.independent_observed_at_ms) > at):
            _refuse()
        base += fill.quantity_base_life if row.side == 'BUY' else -fill.quantity_base_life
        cash += -fill.quantity_base_life * fill.price_usdt if row.side == 'BUY' else fill.quantity_base_life * fill.price_usdt
        fee_events['FEE:' + trade_id] = ('FEE', fill.fee_currency, fill.signed_fee)
    if {k for k in wallet['account_events'] if k.startswith('FEE:')} != set(fee_events):
        _refuse()
    paid = set()
    for key, raw in wallet['account_events'].items():
        if len(raw) != 3:
            _refuse()
        kind, currency, amount = raw[0], raw[1], D(raw[2])
        if key in fee_events:
            if (kind, currency, amount) != fee_events[key]:
                _refuse()
        elif key.startswith('CASHFLOW:'):
            flow = flows.get(key.removeprefix('CASHFLOW:'))
            if (flow is None or (kind, currency, amount) != ('CASHFLOW', flow.currency, flow.signed_amount)
                    or flow.observed_at > observed_at
                    or max(flow.transfer_at_ms, flow.value_at_ms, flow.reference_observed_at_ms) > at):
                _refuse()
        elif key.startswith('JOINT:'):
            event = events.get(key)
            if event is None or event.at_ms > at or (kind, currency, amount) != ('JOINT', 'USDT', event.signed_amount_usdt):
                _refuse()
            paid.add(key)
        else:
            _refuse()
        if currency == 'LIFE':
            base += amount
        elif currency == 'USDT':
            cash += amount
        else:
            _refuse()
    if (base, cash) != (D(wallet['life_balance']), D(wallet['usdt_balance'])):
        _refuse()
    return base, cash, paid


def _after_money(wallet, command, batch, flows, base, cash, resources, owner):
    """Replay only missing original leaves; an old wallet is never a seed."""
    snapshots = {v.prepare_operation_id: v for v in batch.spot_fills} if command.spot_fills else {}
    trades = set(wallet['trades'])
    transfers = {k.removeprefix('CASHFLOW:') for k in wallet['account_events'] if k.startswith('CASHFLOW:')}
    at = int(command.observed_at.timestamp() * 1000)
    for old in command.spot_fills:
        receipt = resources.get('fill_operations', {}).get(old.operation_id)
        if (receipt is None or receipt['state'] != 'APPLIED' or receipt['command'] != old.json()
                or receipt['owner'] != owner
                or receipt['fingerprint'] != fingerprint({'command': old.json(), 'owner': owner})):
            _refuse()
        full = snapshots.get(old.prepare_operation_id)
        excluded = ('operation_id', 'fills', 'cumulative_base_life', 'observed_at')
        if (full is None or old.observed_at > command.observed_at
                or {k: v for k, v in old.json().items() if k not in excluded}
                != {k: v for k, v in full.json().items() if k not in excluded}
                or old.cumulative_base_life > full.cumulative_base_life):
            _refuse()
        if not {key for key, row in wallet['trades'].items() if row[0] == old.intent_id} <= {f.trade_id for f in old.fills}:
            _refuse()
        known = {f.trade_id: f for f in full.fills}
        for fill in old.fills:
            if known.get(fill.trade_id) != fill or max(fill.fill_at_ms, fill.independent_value_at_ms, fill.independent_observed_at_ms) > at:
                _refuse()
            if fill.trade_id not in trades:
                base += fill.quantity_base_life if old.side == 'BUY' else -fill.quantity_base_life
                cash += -fill.quantity_base_life * fill.price_usdt if old.side == 'BUY' else fill.quantity_base_life * fill.price_usdt
                base += fill.signed_fee if fill.fee_currency == 'LIFE' else D('0')
                cash += fill.signed_fee if fill.fee_currency == 'USDT' else D('0')
                trades.add(fill.trade_id)
    for flow in command.spot_cashflows:
        receipt = resources.get('cashflow_operations', {}).get(flow.operation_id)
        if (receipt is None or receipt['state'] != 'APPLIED' or receipt['command'] != flow.json()
                or receipt['fingerprint'] != fingerprint({'command': flow.json(), 'owner': receipt['owner']})
                or flows.get(flow.bill_id) != flow or flow.observed_at > command.observed_at
                or max(flow.transfer_at_ms, flow.value_at_ms, flow.reference_observed_at_ms) > at):
            _refuse()
        if flow.bill_id not in transfers:
            base += flow.signed_amount if flow.currency == 'LIFE' else D('0')
            cash += flow.signed_amount if flow.currency == 'USDT' else D('0')
            transfers.add(flow.bill_id)
    return base, cash, trades, transfers


def funding_history(source, pending, batch, flows, policy):
    resources = json.loads(source.checkpoint_bytes)['state']['resources']
    rows = source.original_operations
    pending_row = rows[pending.operation_id]
    pending_cut = FinancialCut.from_json(pending_row['cut'])
    complete = {e.event_id: e for e in pending.events}
    previous, revisions = [], set()
    for key, row in rows.items():
        old = JointCashSpot.from_json(row['command'])
        cut, owner = FinancialCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
        if (set(row) != {'command', 'cut', 'owner', 'fingerprint', 'state', 'before_native'}
                or key != old.operation_id or old.binding != source.cut.checkpoint.binding
                or cut.checkpoint.binding != old.binding
                or row['fingerprint'] != fingerprint({'command': old.json(), 'cut': cut.json(), 'owner': owner.json()})
                or {name: hashlib.sha256(bytes.fromhex(raw)).hexdigest() for name, raw in row['before_native'].items()} != dict(cut.components)
                or not old.order_scope_complete or not old.bill_scope_complete):
            _refuse()
        if key == pending.operation_id:
            if row['state'] != 'APPLYING':
                _refuse()
            continue
        if row['state'] != 'APPLIED' or cut.checkpoint.revision >= pending_cut.checkpoint.revision or cut.checkpoint.revision in revisions:
            _refuse()
        revisions.add(cut.checkpoint.revision)
        if (old.spot_prepared_receipts
                or old.spot_terminal_operations or old.spot_settlement_operations):
            raise StorageError('PG_LEGACY_APPLYING_PROFILE_UNSUPPORTED')
        _completed_acks(old, resources, row['owner'])
        if any(complete.get(e.event_id) != e or e.event_id.split(':')[2] != 'FUNDING' for e in old.events):
            _refuse()
        previous.append((cut.checkpoint.revision, key, row, old))
    expected, earlier, prior_trades, prior_transfers = {}, set(), set(), set()
    opened = int(datetime.fromisoformat(policy['opened_at']).timestamp() * 1000)
    for _, key, row, old in sorted(previous):
        supplied = {e.event_id: e for e in old.events}
        if not earlier <= set(supplied) or any(not opened <= e.at_ms <= int(old.observed_at.timestamp() * 1000) for e in old.events):
            _refuse()
        wallet = json.loads(bytes.fromhex(row['before_native']['reservations']))
        base, cash, paid = _wallet_money(wallet, old, batch, flows, policy, complete)
        if (not prior_trades <= set(wallet['trades'])
                or not prior_transfers <= {k.removeprefix('CASHFLOW:') for k in wallet['account_events'] if k.startswith('CASHFLOW:')}):
            _refuse()
        base, cash, prior_trades, prior_transfers = _after_money(wallet, old, batch, flows, base, cash, resources, row['owner'])
        if paid != earlier or (old.account_base_life, old.account_quote_usdt) != (
                base, cash + sum((e.signed_amount_usdt for e in old.events if e.event_id not in paid), D('0'))):
            _refuse()
        for event in old.events:
            expected.setdefault(event.event_id, {'event': event.json(), 'operation_id': key})
        earlier = set(supplied)
    if resources.get('joint_cashflows', {}) != expected:
        _refuse()
    before = json.loads(bytes.fromhex(pending_row['before_native']['reservations']))
    if (not prior_trades <= set(before['trades'])
            or not prior_transfers <= {k.removeprefix('CASHFLOW:') for k in before['account_events'] if k.startswith('CASHFLOW:')}):
        _refuse()
    return {key: complete[key] for key in expected}

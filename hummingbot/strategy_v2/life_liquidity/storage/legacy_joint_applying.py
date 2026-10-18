"""Read-only complete financial participant capture for outer File APPLYING.

All images remain diagnostics until signed retirement and native import. No
snapshot/check/reconcile callback that can publish a journal is used here.
"""
import json
from decimal import Decimal
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes

BASE = frozenset(('joint_recovery', 'shared_capital', 'swap_actions', 'swap_wal', 'requests'))


def validate_images(source):
    """Structural/identity proof; the capture port verifies the actual owners."""
    images = source.ancillary_images
    names = tuple(row[0] for row in images)
    if (names != tuple(sorted(set(names))) or not BASE <= set(names)
            or any(type(row) is not tuple or len(row) != 3 or type(row[1]) is not str or type(row[2]) is not bytes
                   or not Path(row[1]).is_absolute() for row in images)
            or len({row[1] for row in images}) != len(images)):
        raise StorageError('LEGACY_JOINT_APPLYING_IMAGES_INVALID')
    raw = {name: json.loads(data) for name, _, data in images}
    paths = {name: path for name, path, _ in images}
    original = json.loads(source.checkpoint_bytes)
    resources = original['state']['resources']
    registry = resources['joint_repository_policy']
    state, policy = raw['joint_recovery']['state'], raw['joint_recovery']['policy']
    if set(registry['products']) != {'carry', 'hedge'}:
        raise StorageError('LEGACY_JOINT_APPLYING_PRODUCT_INVALID')
    expected = BASE | {name for name, digest in registry['products'].items() if digest is not None}
    if (set(names) != expected or registry['binding'] != source.cut.checkpoint.binding.json()
            or not registry.get('coupled_cash') or fingerprint(policy) != registry['native']
            or any(raw[name]['schema_version'] != 1 for name in ('joint_recovery', 'shared_capital', 'swap_actions'))
            or state['phase'] != 'APPLYING' or fingerprint(state['bundle']) != state['proof_id']
            or policy['capital_policy'] != raw['shared_capital']['policy']
            or policy['capital_path'] != paths['shared_capital'] or policy['swap_wal'] != paths['swap_wal']
            or policy['spot_wal'] != original['policy']['legacy_spot']['paths']['wal']
            or policy['reservations'] != original['policy']['legacy_spot']['paths']['reservations']
            or policy['swap_policy'] != raw['swap_actions']['policy']
            or policy['swap_policy']['request_budget'] != paths['requests']
            or raw['requests']['account_uid'] != source.cut.checkpoint.binding.account_uid
            or raw['requests']['policy'] != policy['swap_policy']['request_policy']):
        raise StorageError('LEGACY_JOINT_APPLYING_ORIGIN_INVALID')
    for name, expected_digest in registry['products'].items():
        if expected_digest is not None and fingerprint(raw[name]['policy']) != expected_digest:
            raise StorageError('LEGACY_JOINT_APPLYING_PRODUCT_INVALID')
    bundle = state['bundle']
    if (bundle['account_uid'] != source.cut.checkpoint.binding.account_uid or not bundle['scope_complete']
            or not bundle['runner_scope_complete'] or bundle['source'] != 'okx_joint_reconciled' or bundle['algo_wire_ids']):
        raise StorageError('LEGACY_JOINT_APPLYING_SCOPE_INVALID')
    operation_id = 'joint:' + state['proof_id']
    pending = [key for key, row in source.original_operations.items() if row['state'] == 'APPLYING']
    if pending and pending != [operation_id]:
        raise StorageError('LEGACY_JOINT_APPLYING_OTHER_CORE_PENDING')
    row = source.original_operations.get(operation_id)
    if row is not None:
        command = row['command']
        if command['proof_id'] != state['proof_id'] or row['state'] not in ('APPLYING', 'APPLIED'):
            raise StorageError('LEGACY_JOINT_APPLYING_CASH_ORIGIN_INVALID')
        cash = {event['event_id']: event for event in command['events']}
        for order in bundle['orders']:
            if order['claim']['product'] != 'SWAP':
                continue
            for trade in order['trades']:
                key = f"JOINT:{trade['at_ms']}:TRADE:{trade['trade_id']}"
                expected_event = {'event_id': key, 'signed_amount_usdt': str(Decimal(trade['realized_pnl_quote']) + Decimal(trade['signed_fee'])),
                                  'realized_pnl_usdt': trade['realized_pnl_quote'], 'signed_fee_usdt': trade['signed_fee'],
                                  'session_id': order['claim']['session_id']}
                # Monetary equality retains the original string bytes in the
                # capsule, without requiring equivalent decimals to be spelled
                # in the same way by two independent existing serializers.
                event = cash.get(key)
                if (event is None or event['session_id'] != expected_event['session_id'] or any(
                        Decimal(event[k]) != Decimal(expected_event[k]) for k in (
                            'signed_amount_usdt', 'realized_pnl_usdt', 'signed_fee_usdt'))):
                    raise StorageError('LEGACY_JOINT_APPLYING_TRADE_CHANGED')
        for payment in bundle['payments']:
            key = f"JOINT:{payment['at_ms']}:FUNDING:{payment['bill_id']}"
            if key not in cash or Decimal(cash[key]['signed_amount_usdt']) != Decimal(payment['signed_amount_quote']):
                raise StorageError('LEGACY_JOINT_APPLYING_FUNDING_CHANGED')
    return state


def capture_images(store, joint):
    from hummingbot.strategy_v2.life_liquidity.storage.joint import FileJointRepository
    store.assert_worker_thread()
    if (type(joint) is not FileJointRepository or joint.store is not store or joint.binding != store.binding
            or not joint._couple_cash or joint._recovery._repository_application is not joint):
        raise StorageError('LEGACY_JOINT_APPLYING_REPOSITORY_REQUIRED')
    joint._guard(observation=True)
    recovery, sender = joint._recovery, joint._recovery.sender
    journals = [recovery.journal, recovery.capital.journal, sender.journal, sender.wal]
    journals.extend(v.journal for v in (recovery.controller._hedge_coordinator, recovery.controller._carry_monitor) if v is not None)
    if any(getattr(v, 'archive', None) is not None or getattr(v, '_archive', None) is not None for v in journals):
        raise StorageError('LEGACY_JOINT_APPLYING_ARCHIVE_UNSUPPORTED')

    def verify_joint():
        state = recovery.journal._read()
        recovery._validate_state(state)
        body = state['bundle']
        if body is None:
            raise StorageError('LEGACY_JOINT_APPLYING_SCOPE_INVALID')
        if (len(body['orders']) > joint.limits.max_orders
                or sum(len(row['trades']) for row in body['orders']) > joint.limits.max_trades
                or len(body['payments']) > joint.limits.max_trades):
            raise StorageUnavailable('LEGACY_JOINT_APPLYING_HISTORY_BOUND')
        joint.assert_settlement_capacity(body)

    participants = {
        'joint_recovery': (recovery.journal.path, verify_joint),
        'shared_capital': (recovery.capital.journal.path, lambda: recovery.capital.claims(include_settled=True)),
        'swap_actions': (sender.journal.path, lambda: sender.journal._read()),
        'swap_wal': (sender.wal.path, sender.wal.assert_verified),
        'requests': (sender.request_budget.path, sender.request_budget._read),
    }
    for name, component in (('hedge', recovery.controller._hedge_coordinator), ('carry', recovery.controller._carry_monitor)):
        if component is not None:
            participants[name] = (component.journal.path, lambda native=component: native.journal._read())
    expected = thaw(store._read().resources['joint_repository_policy'])
    if joint._policy() != expected:
        raise StorageError('LEGACY_JOINT_APPLYING_POLICY_CHANGED')
    paths = [str(Path(path).absolute()) for path, _ in participants.values()]
    if len(set(paths)) != len(paths) or set(paths) & {str(p) for p in store._files.__dict__.values() if p is not None}:
        raise StorageError('LEGACY_JOINT_APPLYING_PATH_COLLISION')
    images, remaining = [], store.limits.max_checkpoint_bytes - sum(len(v) for v in store._raw().values())
    try:
        for name, (path, verify) in sorted(participants.items()):
            data = read_bytes(path, max(0, remaining))
            if verify() is False or read_bytes(path, len(data)) != data:
                raise StorageUnavailable('LEGACY_JOINT_APPLYING_SOURCE_CHANGED')
            images.append((name, str(Path(path).absolute()), data))
            remaining -= len(data)
        # Native observation/check callbacks may advance clocks or state. They
        # are deliberately excluded above, and every admitted byte is checked
        # again after all participant validation.
        for name, path, data in images:
            if read_bytes(Path(path), len(data)) != data:
                raise StorageUnavailable('LEGACY_JOINT_APPLYING_SOURCE_CHANGED')
        if joint._policy() != expected:
            raise StorageError('LEGACY_JOINT_APPLYING_POLICY_CHANGED')
    except (ValueError, KeyError, TypeError):
        raise StorageUnavailable('LEGACY_JOINT_APPLYING_UNVERIFIED') from None
    return tuple(images)

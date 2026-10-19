"""Completed File repair history is audit context, never a new monetary grant.

The native prefix checks must still independently verify every original child
receipt, permanent economic ID, value and gross cost. These wrappers cannot
stand for a native receipt, provider authentication or a historical NAV proof.
"""
from collections import deque

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch


def completed_recovery_history(resources, source_facts):
    rows = resources.get('recovery_batches', {})
    result, models, observations = [], {}, {}

    def refuse():
        raise StorageError('PG_LEGACY_APPLYING_ORIGINAL_RECOVERY_HISTORY_REQUIRED')

    for key, row in rows.items():
        if (type(row) is not dict or not {'command', 'fingerprint', 'owner', 'state', 'facts'} <= set(row)
                or set(row) - {'command', 'fingerprint', 'owner', 'state', 'facts', 'completed_by'}):
            refuse()
        command = SpotRecoveryBatch.from_json(row['command'])
        facts = SpotFinancialRecoveryFacts.from_json(row['facts'])
        writer = OwnerToken.from_json(row['owner'])
        if (command.json() != row['command'] or facts.json() != row['facts']
                or key != command.operation_id or row['state'] != 'APPLIED'
                or facts.observed_at < writer.valid_from
                or min(facts.account_base_life, facts.account_quote_usdt) < 0
                or row['fingerprint'] != fingerprint({'command': command.json(), 'owner': row['owner']})
                or facts.binding != source_facts.binding or facts.observed_at > source_facts.observed_at
                or not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or not set(facts.account_trade_ids) <= set(source_facts.account_trade_ids)
                or not set(facts.account_cashflow_ids) <= set(source_facts.account_cashflow_ids)):
            refuse()
        for name, commands, completed in (('fill_operations', command.fills, 'APPLIED'),
                                          ('cashflow_operations', command.cashflows, 'APPLIED'),
                                          ('acknowledgements', command.acknowledgements, 'ACKED')):
            for child in commands:
                actual = resources.get(name, {}).get(child.operation_id)
                if (actual is None or actual['state'] != completed or actual['command'] != child.json()
                        or actual['owner'] != row['owner'] or child.observed_at > facts.observed_at
                        or actual['fingerprint'] != fingerprint({'command': child.json(), 'owner': row['owner']})):
                    refuse()
        if (any(fill.trade_id not in facts.account_trade_ids for group in command.fills for fill in group.fills)
                or any(flow.bill_id not in facts.account_cashflow_ids for flow in command.cashflows)
                or any(group.client_order_id not in facts.accepted_wire_ids for group in command.fills)
                or any(ack.client_order_id not in facts.accepted_wire_ids for ack in command.acknowledgements)):
            refuse()
        models[key], observations[key] = command, facts.observed_at
        # The complete original capsule is retained and signed separately.
        # Retain a digest of each fully checked wrapper here instead of
        # duplicating its command/facts in every native admission readback.
        result.append({'operation_id': key, 'original_fingerprint': row['fingerprint'], 'content_hash': fingerprint(row)})
    dependents = {key: [] for key in models}
    remaining = {}
    for key, command in models.items():
        fills = {row.intent_id: row for row in command.fills}
        cashflows = {row.bill_id: row for row in command.cashflows}
        remaining[key] = len(command.supersedes)
        completer = rows[key].get('completed_by', key)
        for previous in command.supersedes:
            # Several interrupted attempts remain APPLYING until one final
            # batch completes the whole cohort. Earlier attempted supersedes
            # links remain original; completed_by names that final batch.
            if (previous not in rows or rows[previous].get('completed_by') != completer or rows[previous]['owner'] != rows[key]['owner']
                    or observations[previous] > observations[key]):
                refuse()
            dependents[previous].append(key)
            original = models[previous]
            if (any(row.intent_id not in fills or any(fill not in fills[row.intent_id].fills for fill in row.fills)
                    for row in original.fills)
                    or any(cashflows.get(row.bill_id) != row for row in original.cashflows)
                    or any(row not in command.acknowledgements for row in original.acknowledgements)):
                refuse()
        successor = rows[key].get('completed_by')
        if successor is not None and (successor not in models or key not in models[successor].supersedes
                                      or rows[successor].get('completed_by') is not None):
            refuse()
    ready = deque(key for key, count in remaining.items() if not count)
    verified = 0
    while ready:
        verified += 1
        for key in dependents[ready.popleft()]:
            remaining[key] -= 1
            if not remaining[key]:
                ready.append(key)
    if verified != len(models):
        refuse()
    return {'spot_recovery_history': sorted(result, key=lambda row: row['operation_id'])} if result else {}

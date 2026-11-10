"""Conservative recovery proofs over immutable fills; no exchange or DB writes."""
import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

MAX_FILLS = 2000
MAX_ORDERS = 5000
PROOF_TTL_SECONDS = 300
RECOVERY_TIMEOUT_SECONDS = 120
TECHNICAL_HALTS = frozenset({
    'Strategy initialization/reconciliation failed; inspect sanitized logs.',
    'A previous process has unresolved exchange effects; reconcile and verify it is stopped.',
    'Exchange reconciliation did not complete; new risk remains blocked.',
    'An order submission has an unresolved outcome; no automatic resubmission is allowed.',
    'An order submission did not finish with a durable acknowledgment.',
    'Automatic recovery evidence conflicts with its durable account/financial scope.',
})
FINANCIAL_FIELDS = ('amount', 'volume_traded_quote', 'breakeven_price', 'realized_pnl_quote', 'cum_fees_quote')


class RecoveryBlocked(RuntimeError):
    """Only fixed codes cross the operation boundary; driver details stay private."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def decimal(value):
    try:
        result = Decimal(str(value))
        if result.is_finite():
            return result
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise RecoveryBlocked('recovery_financial_data_invalid')


def recovery_blockers(state):
    reasons = []
    if state.get('desired_state') != 'stopped':
        reasons.append('recovery_stop_required')
    if state.get('acknowledged_version') != state.get('control_version'):
        reasons.append('recovery_stop_incomplete')
    recovery = state.get('recovery_state')
    if not isinstance(recovery, dict):
        return reasons + ['recovery_checkpoint_invalid']
    if recovery.get('halt') is not True:
        reasons.append('recovery_not_halted')
    if recovery.get('pending_mutations'):
        reasons.append('recovery_pending_mutations')
    if recovery.get('credential_operation'):
        reasons.append('credential_operation_in_progress')
    if recovery.get('spot_outflow') is not None:
        try:
            from hummingbot.core.spot_outflow_guard import checkpoint_outflow_status
            checkpoint_outflow_status(recovery['spot_outflow'])
        except (ValueError, TypeError, KeyError, ArithmeticError):
            reasons.append('recovery_checkpoint_invalid')
    checkpoints = recovery.get('strategy_checkpoints', {})
    if not isinstance(checkpoints, dict):
        reasons.append('recovery_checkpoint_invalid')
    else:
        for checkpoint in checkpoints.values():
            if not isinstance(checkpoint, dict):
                reasons.append('recovery_checkpoint_invalid')
                continue
            if '_is_stop_triggered' in checkpoint and type(checkpoint['_is_stop_triggered']) is not bool:
                reasons.append('recovery_checkpoint_invalid')
            if checkpoint.get('_is_stop_triggered') is True or checkpoint.get('mm_drawdown_breach') or checkpoint.get('drawdown_exited_controllers'):
                reasons.append('recovery_risk_stop')
            try:
                if 'mm_drawdown_policy' in checkpoint:
                    from hummingbot.core.mm_drawdown import checkpoint_drawdown_status
                    view = checkpoint_drawdown_status(checkpoint)
                    if view is None or view['operator_action_required']:
                        reasons.append('recovery_checkpoint_invalid')
                if 'max_global_pnl' in checkpoint:
                    if decimal(checkpoint['max_global_pnl']) < 0:
                        raise RecoveryBlocked('recovery_checkpoint_invalid')
                peaks = checkpoint.get('max_pnl_by_controller', {})
                if not isinstance(peaks, dict):
                    raise RecoveryBlocked('recovery_checkpoint_invalid')
                for peak in peaks.values():
                    if decimal(peak) < 0:
                        raise RecoveryBlocked('recovery_checkpoint_invalid')
            except RecoveryBlocked:
                reasons.append('recovery_checkpoint_invalid')
    defense = recovery.get('volatility_defense', {})
    if not isinstance(defense, dict):
        reasons.append('recovery_checkpoint_invalid')
    elif defense.get('pauses'):
        reasons.append('recovery_risk_stop')
    reason = recovery.get('reason')
    if not isinstance(reason, str) or reason not in TECHNICAL_HALTS:
        reasons.append('recovery_risk_stop' if isinstance(reason, str) and any(
            word in reason.lower() for word in ('drawdown', 'risk', 'outflow', 'bootstrap', 'volatility')) else 'recovery_reason_unsupported')
    return sorted(set(reasons))


def fingerprint(snapshot):
    data = {key: snapshot.get(key, []) for key in ('orders', 'fills', 'positions', 'bindings')}
    state = snapshot['state']
    data['state'] = {key: state.get(key) for key in (
        'desired_state', 'control_version', 'acknowledged_version', 'owner_uuid', 'owner_generation',
        'config_file_path', 'config_type', 'mm_session')}
    data['state']['recovery_state'] = {key: value for key, value in (state.get('recovery_state') or {}).items()
                                       if key not in ('funding_sync', 'recovery_operation')}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()


def replay_positions(snapshot):
    """Only extend a checkpoint already proved to be a prefix of its own fills."""
    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
    if snapshot.get('truncated') or len(snapshot['fills']) > MAX_FILLS or len(snapshot.get('orders', [])) > MAX_ORDERS:
        raise RecoveryBlocked('recovery_scope_limit')
    scopes = [checkpoint['mm_drawdown_policy']['scope']
              for checkpoint in (snapshot['state'].get('recovery_state', {}).get('strategy_checkpoints') or {}).values()
              if isinstance(checkpoint, dict) and isinstance(checkpoint.get('mm_drawdown_policy'), dict)]
    positions = {}
    for row in snapshot['positions']:
        key = row['connector_name'], row['trading_pair'], row['side'] if row['connector_name'] == 'okx_perpetual' else 'NET'
        if not row['controller_id'] or not row['trading_pair'].endswith('-USDT') or key in positions or row['connector_name'] not in ('okx', 'okx_perpetual') or row['side'] not in ('BUY', 'SELL'):
            raise RecoveryBlocked('recovery_position_scope_ambiguous')
        if scopes and any(scope.get(row['controller_id']) != dict(connector_name=key[0], trading_pair=key[1]) for scope in scopes):
            raise RecoveryBlocked('recovery_position_scope_ambiguous')
        positions[key] = row
    grouped = defaultdict(list)
    identities = set()
    for fill in snapshot['fills']:
        identity = fill['market'], fill['order_id'], fill['exchange_trade_id']
        if identity in identities:
            raise RecoveryBlocked('recovery_duplicate_fill')
        identities.add(identity)
        side = fill['trade_type']
        if side not in ('BUY', 'SELL') or decimal(fill['amount']) <= 0 or decimal(fill['price']) <= 0:
            raise RecoveryBlocked('recovery_financial_data_invalid')
        leg = side
        if fill['market'] == 'okx_perpetual':
            if fill.get('position') not in ('OPEN', 'CLOSE'):
                raise RecoveryBlocked('recovery_position_scope_ambiguous')
            if fill['position'] == 'CLOSE':
                leg = 'SELL' if side == 'BUY' else 'BUY'
        key = fill['market'], fill['symbol'], leg if fill['market'] == 'okx_perpetual' else 'NET'
        if key not in positions:
            raise RecoveryBlocked('recovery_position_scope_ambiguous')
        grouped[key].append(fill)
    plan = []
    for key, stored in positions.items():
        hold = PositionHold(key[0], key[1], TradeType[stored['side']])

        def summary():
            view = hold.get_position_summary(hold.avg_entry_price)
            values = {field: str(getattr(view, field)) for field in FINANCIAL_FIELDS}
            values.update(side=view.side.name if hold.net_amount_base else stored['side'], unrealized_pnl_quote='0')
            return values

        def matches(values):
            return all(decimal(stored[field]) == decimal(values[field]) for field in FINANCIAL_FIELDS) and (
                not decimal(values['amount']) or stored['side'] == values['side'])

        prefix_proven = matches(summary())
        fills = sorted(grouped[key], key=lambda row: (row['timestamp'], row['exchange_trade_id'], row['order_id']))
        for fill in fills:
            if key[0] == 'okx_perpetual' and fill['position'] == 'CLOSE' and decimal(fill['amount']) > abs(hold.net_amount_base):
                raise RecoveryBlocked('recovery_position_scope_ambiguous')
            fee = fill['trade_fee']
            if not isinstance(fee, dict) or not isinstance(fee.get('flat_fees', []), list):
                raise RecoveryBlocked('recovery_fee_basis_unsupported')
            if decimal(fee.get('percent', '0')):
                raise RecoveryBlocked('recovery_fee_basis_unsupported')
            actual_fee = Decimal(0)
            for item in fee.get('flat_fees', []):
                if not isinstance(item, dict) or 'amount' not in item:
                    raise RecoveryBlocked('recovery_fee_basis_unsupported')
                if item.get('token') == 'USDT':
                    actual_fee += decimal(item['amount'])
                elif key[0] == 'okx' and item.get('token') == key[1].removesuffix('-USDT'):
                    # A spot base-asset fee has an exact quote equivalent at
                    # this fill's verified price. No live price or other asset
                    # conversion is introduced; quantity remains gross filled.
                    actual_fee += decimal(item['amount']) * decimal(fill['price'])
                else:
                    raise RecoveryBlocked('recovery_fee_basis_unsupported')
            if fill.get('trade_fee_in_quote') is None or actual_fee != decimal(fill['trade_fee_in_quote']):
                raise RecoveryBlocked('recovery_fee_basis_unsupported')
            identity = str(fill['order_id']) + ':' + str(fill['exchange_trade_id'])
            order = dict(client_order_id=identity, trade_type=fill['trade_type'], executed_amount_base=fill['amount'],
                         executed_amount_quote=decimal(fill['amount']) * decimal(fill['price']), cumulative_fee_paid_quote=actual_fee)
            hold.add_orders_from_executor(SimpleNamespace(id=identity, custom_info={'held_position_orders': [order]},
                                                          config=SimpleNamespace(side=TradeType[fill['trade_type']])))
            prefix_proven = prefix_proven or matches(summary())
        if not prefix_proven:
            raise RecoveryBlocked('position_baseline_unproven')
        after = summary()
        if not matches(after):
            plan.append(dict(id=stored['id'], controller_id=stored['controller_id'], connector=key[0], pair=key[1],
                             before={field: str(stored[field]) for field in (*FINANCIAL_FIELDS, 'side', 'unrealized_pnl_quote')},
                             after=after, valuation_basis='reconstructed_average_entry_not_live_pnl'))
    return deepcopy(plan)

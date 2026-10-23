"""Detached original-basis capital projection for a stopped File import.

This is a parity input, not a target initializer or a complete product verifier.
Its source must come from the trusted frozen artifact decoder. It never opens
embedded paths, advances session time, rebases capital or creates permission.
Every original resource/journal remains in the source artifact for translation.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.journal_archive import JournalArchive
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.capital_replay import replay_capital
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot, SpotFillObservation, decimal
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent
from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
from hummingbot.strategy_v2.life_liquidity.storage.service_source import StoppedFileServiceSource


@dataclass(frozen=True)
class StoppedCapitalProjection:
    body: bytes
    trading_permission: bool = field(default=False, init=False)

    def json(self):
        return decode(self.body)


def _journal(source, name):
    archive = dict(source.archives).get('core.' + name)
    if archive is None:
        return decode(dict(source.base.core.journal_images)[name])
    image = archive.materialize()
    if name in ('wal', 'reservations'):
        if image['policy'].get('journal') != name:
            raise StorageError('STOPPED_CAPITAL_JOURNAL_POLICY_INVALID')
        return JournalArchive.join_state(image['state'], kind=name, limits=image['policy']['limits'])
    return {'schema_version': 2, 'policy': image['policy'], **image['state']}


def project_stopped_capital(source, *, max_rows):
    if (type(source) is not StoppedFileServiceSource or type(max_rows) is not int or not 0 < max_rows <= 1000000):
        raise StorageError('STOPPED_CAPITAL_REQUEST_INVALID')
    try:
        # Replay the original File arithmetic profile. A maintenance caller's
        # Decimal settings must not reprice old fills or alter their cost floors.
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            return _project(source, max_rows)
    except StorageError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        raise StorageError('STOPPED_CAPITAL_UNVERIFIED') from None


def _budget_projection(source, config, resources, max_rows):
    """Preserve every period and original hold, without settling at import time."""
    try:
        return _budgets(source, config, resources, max_rows)
    except StorageError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
        raise StorageError('STOPPED_BUDGET_UNVERIFIED') from None


def _budgets(source, config, resources, max_rows):
    prepared = {row['command']['intent_id']: PrepareSpot.from_json(row['command'])
                for row in resources.get('commands', {}).values()}
    if len(prepared) != len(resources.get('commands', {})):
        raise StorageError('STOPPED_BUDGET_INTENT_CONFLICT')
    floors, expected_loss = {}, {}
    for key, row in resources.get('fills', {}).items():
        fill = SpotFillObservation.from_json(row['fill'])
        order = prepared[row['intent_id']]
        amount = fill.loss(order.side)
        floors[order.intent_id] = floors.get(order.intent_id, Decimal('0')) + amount
        expected_loss['fill:' + key] = dict(session_id=order.session_id,
                                            day=datetime.fromtimestamp(fill.fill_at_ms / 1000, timezone.utc).date().isoformat(),
                                            loss_quote=str(amount))
    for row in resources.get('joint_cashflows', {}).values():
        event = JointCashEvent.from_json(row['event'])
        if event.session_id is not None:
            key = 'swap:' + event.event_id.split(':')[3]
            value = dict(session_id=event.session_id,
                         day=datetime.fromtimestamp(event.at_ms / 1000, timezone.utc).date().isoformat(),
                         loss_quote=str(event.execution_loss_usdt))
            if key in expected_loss:
                raise StorageError('STOPPED_BUDGET_LOSS_ID_CONFLICT')
            expected_loss[key] = value
    result, total_rows = {}, 0
    for name, policy, scope in (('loss', config.loss_policy, None), ('subsidy', config.subsidy_policy, 'QUOTE'),
                                ('exit_subsidy', None if config.exit is None else config.exit.budget, 'EXIT')):
        if policy is None:
            if any(order.budget_scope == scope for order in prepared.values()) or name in dict(source.base.core.journal_images):
                raise StorageError('STOPPED_BUDGET_POLICY_MISSING')
            result[name] = None
            continue
        journal = _journal(source, name)
        branch = 'events' if name == 'loss' else 'entries'
        expected_policy = {k: v for k, v in policy.json().items() if k != 'campaign_id'}
        if (set(journal) != {'schema_version', 'campaign_id', 'policy', branch} or journal['schema_version'] != 2
                or journal['campaign_id'] != policy.campaign_id or journal['policy'] != expected_policy
                or type(journal[branch]) is not dict):
            raise StorageError('STOPPED_BUDGET_POLICY_MISMATCH')
        rows = journal[branch]
        total_rows += len(rows)
        if total_rows > max_rows:
            raise StorageError('STOPPED_BUDGET_CAPACITY_EXCEEDED')
        if name == 'loss':
            for key, expected in expected_loss.items():
                actual = rows.get(key)
                if (actual is None or set(actual) != set(expected)
                        or (actual['session_id'], actual['day'], Decimal(actual['loss_quote'])) !=
                        (expected['session_id'], expected['day'], Decimal(expected['loss_quote']))):
                    raise StorageError('STOPPED_BUDGET_LOSS_MISMATCH')
            if any(key.startswith(('fill:', 'swap:')) and key not in expected_loss for key in rows):
                raise StorageError('STOPPED_BUDGET_LOSS_ID_CONFLICT')
        else:
            SubsidyBudgetLedger._validated_entries(rows)
            if set(rows) != {key for key, order in prepared.items() if order.budget_scope == scope}:
                raise StorageError('STOPPED_BUDGET_INTENT_CONFLICT')
            for key, row in rows.items():
                order = prepared[key]
                if (row['session_id'] != order.session_id or Decimal(row['reserved']) != order.subsidy_quote_usdt
                        or Decimal(row.get('fill_floor', '0')) != floors.get(key, Decimal('0'))):
                    raise StorageError('STOPPED_BUDGET_FILL_FLOOR_MISMATCH')
        campaign, sessions, days = Decimal('0'), {}, {}
        for key, row in rows.items():
            if (type(key) is not str or not key or type(row['session_id']) is not str or not row['session_id']
                    or date.fromisoformat(row['day']).isoformat() != row['day']
                    or date.fromisoformat(row['day']) > source.base.core.facts.observed_at.date()
                    or name == 'loss' and set(row) != {'session_id', 'day', 'loss_quote'}):
                raise StorageError('STOPPED_BUDGET_ENTRY_INVALID')
            amount = Decimal(row['loss_quote']) if name == 'loss' else SubsidyBudgetLedger._committed(row)
            if not amount.is_finite() or amount < 0:
                raise StorageError('STOPPED_BUDGET_AMOUNT_INVALID')
            # Sum retained rows exactly, without recomputing their historical
            # amounts. The DTO's 64-digit/exponent bound and one-million-row
            # ceiling need at most 198 significant digits for these additions.
            decimal(amount)
            with localcontext(Context(prec=200, rounding=ROUND_HALF_EVEN)):
                campaign += amount
                sessions[row['session_id']] = sessions.get(row['session_id'], Decimal('0')) + amount
                days[row['day']] = days.get(row['day'], Decimal('0')) + amount
        result[name] = dict(journal=journal, totals=dict(campaign=str(campaign),
                                                         by_session={k: str(v) for k, v in sessions.items()},
                                                         by_day={k: str(v) for k, v in days.items()}))
    return result


def _project(source, max_rows):
    core = source.base.core
    config = FileServiceConfig.model_validate_json(source.base.service_json)
    checkpoint = decode(core.checkpoint_bytes)
    policy = checkpoint['policy']['legacy_spot']
    resources = checkpoint['state']['resources']
    if (config.controller.storage_binding != core.cut.checkpoint.binding
            or policy['accounting_policy'] != config.accounting_policy.json()
            or policy['risk'] != {key: str(value) for key, value in config.risk_limits.__dict__.items()}
            or policy['fill_policy'] != {'max_reference_skew_ms': config.fill_policy.max_reference_skew_ms}):
        raise StorageError('STOPPED_CAPITAL_POLICY_MISMATCH')
    monetary = {key: resources.get(key, {}) for key in ('commands', 'fills', 'cashflows', 'joint_cashflows', 'nav_operations')}
    if (any(type(value) is not dict for value in monetary.values()) or sum(map(len, monetary.values())) > max_rows
            or sum(archive.record_count for _, archive in source.archives) > max_rows):
        raise StorageError('STOPPED_CAPITAL_CAPACITY_EXCEEDED')
    facts = core.facts
    if (core.unresolved_intents or facts.accepted_wire_ids or facts.runner_wire_ids
            or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
            or not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))
            or set(monetary['fills']) != set(facts.account_trade_ids)
            or set(monetary['cashflows']) != set(facts.account_cashflow_ids)):
        raise StorageError('STOPPED_CAPITAL_RECONCILIATION_INCOMPLETE')
    approvals = {row.bill_id: row for row in config.accounting_policy.approved_cashflows}
    extra = resources.get('additional_cashflow_approvals', {})
    if len(extra) > max_rows:
        raise StorageError('STOPPED_CAPITAL_CAPACITY_EXCEEDED')
    for key, value in extra.items():
        row = ApprovedSpotCashflow(value['bill_id'], value['currency'], Decimal(value['signed_amount']), value['transfer_at_ms'])
        if (key != row.bill_id or key in approvals and approvals[key] != row
                or int(row.bill_id) <= int(config.accounting_policy.anchor_bill_id)
                or row.transfer_at_ms < int(config.accounting_policy.opened_at.timestamp() * 1000)):
            raise StorageError('STOPPED_CAPITAL_APPROVAL_CONFLICT')
        approvals[key] = row

    def validate_cashflow(row):
        approved = approvals.get(row.bill_id)
        receipt_ms = int(row.observed_at.timestamp() * 1000)
        if (approved is None or (approved.currency, approved.signed_amount, approved.transfer_at_ms) != (
                row.currency, row.signed_amount, row.transfer_at_ms)
                or row.transfer_at_ms > receipt_ms or row.reference_observed_at_ms > receipt_ms
                or row.observed_at > facts.observed_at
                or abs(row.value_at_ms - row.transfer_at_ms) > config.fill_policy.max_reference_skew_ms
                or abs(row.reference_observed_at_ms - row.transfer_at_ms) > config.fill_policy.max_reference_skew_ms):
            raise StorageError('STOPPED_CAPITAL_CASHFLOW_UNQUALIFIED')

    capital = replay_capital(config.accounting_policy, monetary['commands'], monetary['fills'], monetary['cashflows'],
                             monetary['joint_cashflows'], validate_cashflow=validate_cashflow)
    wallet = _journal(source, 'reservations')
    if sum(len(wallet.get(key, {})) for key in ('reservations', 'trades', 'account_events')) > max_rows:
        raise StorageError('STOPPED_CAPITAL_CAPACITY_EXCEEDED')
    ledger = ReservationLedger._restore_snapshot(encode(wallet), path=Path('/detached-evidence-never-opened'),
                                                 limits=config.risk_limits, max_reservations=max_rows, max_trades=max_rows)
    if (any(row['state'] != 'TERMINAL' for row in wallet['reservations'].values())
            or ledger.trade_ids != set(monetary['fills'])
            or capital.life_balance != ledger.life_balance or capital.usdt_balance != ledger.usdt_balance
            or (capital.life_balance, capital.usdt_balance) != (facts.account_base_life, facts.account_quote_usdt)
            or ledger.cashflow_events != {key: (row['currency'], Decimal(row['signed_amount'])) for key, row in monetary['cashflows'].items()}
            or ledger.joint_cash_events != {key: Decimal(row['event']['signed_amount_usdt']) for key, row in monetary['joint_cashflows'].items()}):
        raise StorageError('STOPPED_CAPITAL_WALLET_MISMATCH')
    for key, value in monetary['fills'].items():
        fill = SpotFillObservation.from_json(value['fill'])
        prepared = PrepareSpot.from_json(monetary['commands'][value['prepare_operation_id']]['command'])
        if (fill.trade_id != key or value['intent_id'] != prepared.intent_id
                or fill.fill_at_ms > int(facts.observed_at.timestamp() * 1000)
                or fill.independent_observed_at_ms > int(facts.observed_at.timestamp() * 1000)
                or abs(fill.independent_value_at_ms - fill.fill_at_ms) > config.fill_policy.max_reference_skew_ms
                or abs(fill.independent_observed_at_ms - fill.fill_at_ms) > config.fill_policy.max_reference_skew_ms
                or not ledger.matches_recorded_fill(prepared.intent_id, key, fill.quantity_base_life,
                                                    fill.price_usdt, fill.fee_currency, fill.signed_fee)):
            raise StorageError('STOPPED_CAPITAL_FILL_MISMATCH')

    # Historical NAV observations are retained verbatim. A later wallet cannot
    # recompute a prior observation; use the original monotonically retained
    # highwater, never a new measurement at the import clock/price.
    nav = monetary['nav_operations']
    header = resources.get('capital', {})
    highwater = capital.highwater_quote
    if nav:
        if header.get('latest_operation_id') not in nav:
            raise StorageError('STOPPED_CAPITAL_NAV_INCOMPLETE')
        values = [decimal(Decimal(row['measurement']['highwater_usdt']), positive=True) for row in nav.values()]
        highwater = decimal(Decimal(header['highwater_usdt']), positive=True)
        if (highwater != max(values) or highwater < capital.highwater_quote
                or highwater != Decimal(nav[header['latest_operation_id']]['measurement']['highwater_usdt'])):
            raise StorageError('STOPPED_CAPITAL_HIGHWATER_MISMATCH')
    elif header:
        raise StorageError('STOPPED_CAPITAL_NAV_INCOMPLETE')
    body = dict(format='LIFE_STOPPED_CAPITAL_V1', source_digest=source.content_digest,
                source_facts_sha256=fingerprint(facts.json()), accounting_policy=config.accounting_policy.json(),
                balances={'LIFE': str(capital.life_balance), 'USDT': str(capital.usdt_balance)},
                capital=dict(opening_inventory_remaining=str(capital.opening_inventory_remaining),
                             realized_starting_inventory_pnl_usdt=str(capital.realized_starting_inventory_pnl_quote),
                             net_cashflows_usdt=str(capital.net_cashflows_quote), execution_loss_usdt=str(capital.execution_loss_quote),
                             highwater_usdt=str(highwater)),
                trade_ids=sorted(monetary['fills']), cashflow_ids=sorted(monetary['cashflows']),
                joint_cash_ids=sorted(monetary['joint_cashflows']), nav_history=nav,
                budgets=_budget_projection(source, config, resources, max_rows))
    return StoppedCapitalProjection(encode(body))

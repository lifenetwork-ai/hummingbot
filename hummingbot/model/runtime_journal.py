"""Durable native financial work and immutable exchange submission attempts.

No historical backfill is inferred by creating these tables. Existing position
rows remain the baseline accepted by the normal, fenced startup reconciliation.
"""
import hashlib
import json
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Boolean, Column, Index, Sequence, Text, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB

from hummingbot.model import HummingbotBase


class RuntimeJournal(HummingbotBase):
    __tablename__ = 'runtime_journal'
    __table_args__ = (UniqueConstraint('event_key'),
                      Index('idx_runtime_journal_kind_applied_id', 'kind', 'applied', 'id'),
                      Index('idx_runtime_journal_order_id_id', 'order_id', 'id'))

    id = Column(BigInteger, Sequence('seq_runtime_journal_id'), primary_key=True, nullable=False)
    event_key = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    order_id = Column(Text)
    applied = Column(Boolean, nullable=False)
    payload = Column(JSONB().with_variant(JSON(), 'sqlite'), nullable=False)


class SubmissionAttempt(HummingbotBase):
    __tablename__ = 'submission_attempts'
    __table_args__ = (Index('idx_submission_attempts_state_order_id', 'state', 'order_id'),)

    order_id = Column(Text, primary_key=True, nullable=False)
    state = Column(Text, nullable=False)
    context = Column(JSONB().with_variant(JSON(), 'sqlite'), nullable=False)
    payload_hash = Column(Text)
    deadline_ms = Column(BigInteger)
    receipt = Column(JSONB().with_variant(JSON(), 'sqlite'))


class RuntimeCheckpoint(HummingbotBase):
    __tablename__ = 'runtime_checkpoints'
    config_file_path = Column(Text, primary_key=True, nullable=False)
    financial_hash = Column(Text, nullable=False)
    bindings_hash = Column(Text, nullable=False)
    watermark = Column(BigInteger, nullable=False)
    payload = Column(JSONB().with_variant(JSON(), 'sqlite'), nullable=False)


def financial_hash(session):
    from hummingbot.model.position import Position
    names = ('controller_id', 'connector_name', 'trading_pair', 'side', 'amount',
             'breakeven_price', 'realized_pnl_quote', 'volume_traded_quote', 'cum_fees_quote')

    def numeric(value):
        if not value.is_finite():
            raise ValueError('Non-finite position cannot be a recovery baseline')
        text = format(value, 'f')
        return text.rstrip('0').rstrip('.') if '.' in text else text
    rows = session.execute(select(Position).order_by(Position.id).limit(101)).scalars().all()
    if len(rows) > 100:
        raise ValueError('Position recovery exceeds its bounded scope')
    values = [[numeric(getattr(row, name)) if name not in names[:4] else getattr(row, name)
               for name in names] for row in rows]
    return digest(sorted(values))


def bindings(session, bot_id):
    from hummingbot.model.exchange_credential import ExchangeCredential
    return [dict(row) for row in session.execute(select(
        ExchangeCredential.connector, ExchangeCredential.environment, ExchangeCredential.account_id,
        ExchangeCredential.version).where(ExchangeCredential.bot_id == bot_id,
                                          ExchangeCredential.state == 'active')).mappings()]


def advance_checkpoints(session, checkpoints):
    """Advance verified financial work on the caller's existing transaction."""
    if not checkpoints:
        return
    session.flush()
    current = financial_hash(session)
    pending = session.query(RuntimeJournal.id).filter(RuntimeJournal.kind == 'fill',
                                                      RuntimeJournal.applied.is_(False)).order_by(RuntimeJournal.id).first()
    watermark = pending[0] - 1 if pending else session.query(func.max(RuntimeJournal.id)).scalar() or 0
    for checkpoint in checkpoints:
        checkpoint.financial_hash = current
        checkpoint.watermark = watermark


def environment_identity():
    from hummingbot.connector.okx_environment import OKXEnvironment
    environment = OKXEnvironment.load()
    return {'mode': environment.mode, 'site': environment.site}


def binding_hash(session, bot_id):
    return digest({'environment': environment_identity(), 'bindings': sorted(bindings(session, bot_id), key=digest)})


def credential_fingerprint(connector):
    auth = connector.authenticator
    key = getattr(auth, 'api_key', None) or getattr(auth, '_api_key', None)
    return digest(['credential_identity', environment_identity(), connector.name, key]) if isinstance(key, str) and key else None


def can_bootstrap(session, state):
    """Check outstanding work and its verified checkpoint, never lifetime history."""
    from hummingbot.core.automatic_recovery import MAX_ATTEMPTS, eligible
    from hummingbot.model.order import Order
    if state['config_type'] != 'mm-session' or not state['config_file_path']:
        return False
    recovery = state.get('recovery_state') or {}
    if recovery.get('runtime_journal_version') != 1 or recovery.get('journal_owner_generation') != state['owner_generation']:
        return False
    checkpoint = session.get(RuntimeCheckpoint, state['config_file_path'])
    risk = recovery.get('strategy_checkpoints') or {}
    if not isinstance(risk, dict):
        return False
    try:
        current_financial_hash = financial_hash(session) if checkpoint else None
    except ValueError:
        return False
    if (any(value.get('_is_stop_triggered') or value.get('drawdown_exited_controllers') or value.get('mm_drawdown_breach')
            for value in risk.values() if isinstance(value, dict))
            or state['config_type'] != 'mm-session' or not checkpoint
            or checkpoint.financial_hash != current_financial_hash
            or checkpoint.bindings_hash != binding_hash(session, state['bot_id'])):
        return False
    attempts = session.execute(select(SubmissionAttempt).where(SubmissionAttempt.state.in_(
        ('prepared', 'possibly_sent', 'uncertain', 'acknowledged'))).limit(MAX_ATTEMPTS + 1)).scalars().all()
    pending = session.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(MAX_ATTEMPTS + 1)).scalars().all()
    fills = session.execute(select(RuntimeJournal.order_id).where(RuntimeJournal.kind == 'fill',
                                                                  RuntimeJournal.applied.is_(False)).distinct().limit(MAX_ATTEMPTS + 1)).scalars().all()
    ids = {row.order_id for row in attempts}
    if fills or pending:
        attempts += session.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id.in_((set(fills) | set(pending)) - ids))).scalars().all()
        ids.update(row.order_id for row in attempts)
    if (len(attempts) > MAX_ATTEMPTS or not (set(pending) | set(fills)) <= ids
            or any(not eligible(row.context) or row.context['bot_id'] != state['bot_id']
                   or row.context['config_file_path'] != state['config_file_path'] for row in attempts)):
        return False
    return True


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def held_order_snapshot(order):
    """Exact native held-order payload; unavailable quote fees cannot become zero."""
    if (sum((trade.fill_base_amount for trade in order.order_fills.values()), Decimal(0)) != order.executed_amount_base
            or sum((trade.fill_quote_amount for trade in order.order_fills.values()), Decimal(0)) != order.executed_amount_quote):
        raise ValueError('Held position execution coverage is unavailable')
    value = order.to_json(include_fee_totals=False)
    fee = sum((trade.fee.fee_amount_in_token(trading_pair=order.trading_pair, price=trade.fill_price,
                                             order_amount=trade.fill_base_amount, token=order.quote_asset)
               for trade in order.order_fills.values()), Decimal(0))
    if not fee.is_finite():
        raise ValueError('Held position fee valuation is unavailable')
    value['cumulative_fee_paid_quote'] = str(fee)
    return value


def journal_fill(session, fill):
    """Same transaction as the immutable fill; duplication stays with its PK."""
    identity = fill.order_id or fill.order.id
    key = digest([fill.market, identity, fill.exchange_trade_id])
    payload = {name: str(getattr(fill, name)) for name in ('price', 'amount')}
    payload.update(market=fill.market, symbol=fill.symbol, trade_type=fill.trade_type,
                   timestamp=fill.timestamp, exchange_trade_id=fill.exchange_trade_id,
                   trade_fee=fill.trade_fee,
                   trade_fee_in_quote=None if fill.trade_fee_in_quote is None else str(fill.trade_fee_in_quote))
    # Persist a late financial effect before escalating; never discard its fill
    # behind an already-consumed executor identity.
    late = bool(fill.order.position_applied)
    payload['late_application'] = late
    session.add(RuntimeJournal(event_key='fill:' + key, kind='fill', order_id=identity,
                               applied=fill.order.cleanup_operation_id is not None, payload=payload))
    return late


def applied_executors(session, identities):
    if not identities:
        return set()
    keys = ['hold:' + identity for identity in identities]
    return {key.removeprefix('hold:') for key in session.execute(select(RuntimeJournal.event_key).where(
        RuntimeJournal.event_key.in_(keys), RuntimeJournal.kind == 'hold', RuntimeJournal.applied.is_(True))).scalars()}


def prepare_attempt(session, order, owner, connector):
    from hummingbot.core.order_submission_guard import current_executor_scope, current_session_scope
    from hummingbot.model.exchange_credential import ExchangeCredential
    scope = current_session_scope()
    config = current_executor_scope()
    bindings = [dict(row) for row in session.execute(select(
        ExchangeCredential.connector, ExchangeCredential.environment, ExchangeCredential.account_id,
        ExchangeCredential.version).where(ExchangeCredential.bot_id == owner.store.bot_id,
                                          ExchangeCredential.state == 'active')).mappings()]
    context = dict(bot_id=owner.store.bot_id, owner_uuid=owner.owner_uuid, generation=owner.generation,
                   control_version=owner.version, config_file_path=owner.config_file_path,
                   session=list(scope) if scope else None, executor=config,
                   environment=environment_identity(),
                   credential_fingerprint=credential_fingerprint(connector),
                   cleanup=order.client_order_id in owner.cleanup_intents,
                   order=order.to_json(), bindings=bindings,
                   reservation=dict(base=str(order.amount),
                                    quote=str(order.amount * order.price) if order.price is not None and order.price.is_finite() else None,
                                    side=order.trade_type.name))
    # Financial history contains no keys, passphrases or authenticated HTTP headers.
    if len(json.dumps(context, allow_nan=False).encode()) > 65536:
        raise ValueError('Submission context exceeds the bounded record size')
    session.add(SubmissionAttempt(order_id=order.client_order_id, state='prepared', context=context))
    from hummingbot.core.automatic_recovery import eligible
    order._managed_recovery_order = eligible(context)
    session.add(RuntimeJournal(event_key='intent:' + order.client_order_id, kind='intent',
                               order_id=order.client_order_id, applied=True, payload={'executor_id': config.get('id') if config else None}))

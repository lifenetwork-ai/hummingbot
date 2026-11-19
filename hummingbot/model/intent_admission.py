"""Recognize a rejected ownership lock before any order writes.

The proof belongs to one ORM transaction and is consumed after its actual
DBAPI rollback. Once the fence is acquired, write and COMMIT failures remain fatal.
"""
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy.exc import OperationalError

_KEY = 'order_prewrite_fence_rollback'


@dataclass
class _PrewriteRollback:
    transaction: object
    connection: object
    driver: object
    error: object
    confirmed: bool = False


@contextmanager
def capture_prewrite_lock_failure(session):
    connection = session.connection()
    driver = connection.connection.driver_connection
    try:
        yield
    except OperationalError as error:
        if (getattr(error.orig, 'pgcode', None) == '55P03'
                and not error.connection_invalidated and not connection.invalidated):
            session.info[_KEY] = _PrewriteRollback(session.get_transaction(), connection, driver, error)
        raise


def confirm_prewrite_rollback(session, previous_transaction):
    proof = session.info.get(_KEY)
    if not isinstance(proof, _PrewriteRollback) or previous_transaction is not proof.transaction:
        return False
    from psycopg2.extensions import TRANSACTION_STATUS_IDLE

    try:
        proof.confirmed = (not proof.connection.invalidated and not proof.driver.closed
                           and proof.driver.get_transaction_status() == TRANSACTION_STATUS_IDLE)
    except Exception:
        proof.confirmed = False
    return proof.confirmed


def confirmed_prewrite_failure(session, error):
    proof = session.info.pop(_KEY, None)
    return proof is not None and error is proof.error and proof.confirmed


@contextmanager
def order_intent_transaction(manager, order_id):
    """Fence eagerly/NOWAIT before staging even the first Order INSERT."""
    from hummingbot.core.order_submission_guard import IntentAdmissionBusy, active_guard
    from hummingbot.model.order import Order

    with manager.get_new_session() as session:
        try:
            with session.begin():
                # Preserve the original duplicate-identity rejection before the
                # fence. Contention must not excuse an existing durable order.
                if session.get(Order, order_id) is not None:
                    raise RuntimeError('Client order identity already exists; reconcile instead of resubmitting.')
                if manager.engine.dialect.name == 'postgresql':
                    guard = active_guard()
                    if guard is not None:
                        manager.assert_persistence_healthy()
                        if guard.engine is not manager.engine:
                            raise RuntimeError('Intent recorder does not belong to the trading owner.')
                        with capture_prewrite_lock_failure(session):
                            guard.fence_session(session, nowait=True)
                yield session
        except BaseException as error:
            if confirmed_prewrite_failure(session, error) and manager.persistence_healthy:
                raise IntentAdmissionBusy('Intent admission waits for the ownership row; no order was sent.') from None
            # A rollback failure can prevent after_soft_rollback from firing.
            # Keep every other PostgreSQL failure fatal even in that case.
            if manager.engine.dialect.name == 'postgresql':
                manager.persistence_healthy = False
            raise

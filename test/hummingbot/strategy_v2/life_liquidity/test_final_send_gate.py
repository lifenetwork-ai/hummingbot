"""P4.13 offline final-send permit checks old epoch after queue delay."""

from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_session import FakeClock, begin, manager

from hummingbot.strategy_v2.life_liquidity.send_gate import FinalSendGate, SendPermit


def permit(active):
    record = active.current_session
    return SendPermit(intent_id="i1", client_order_id="wire-1", reservation_id="r1",
                      session_id=record.session_id, epoch=record.epoch,
                      config_version=record.config_version, risk_epoch=1,
                      price_usdt=Decimal("1"), quantity_base=Decimal("1"))


def authorize(gate, item, **changes):
    values = dict(config_version=1, risk_epoch=1, reference_ready=True,
                  all_gates_ready=True, market_reference_ready=False,
                  safety_state="NORMAL", economics_allowed=True,
                  reservation_active=True, price_usdt=Decimal("1"),
                  quantity_base=Decimal("1"))
    values.update(changes)
    return gate.authorize(item, **values)


def test_expiry_between_approval_and_final_send_blocks_old_epoch(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    item = permit(active)
    gate = FinalSendGate(active)
    assert authorize(gate, item).allowed
    clock.advance(10)
    expired = authorize(gate, item)
    assert not expired.allowed and expired.reason_code == "SESSION_PERMISSION_REVOKED"


def test_config_risk_and_order_mutation_revoke_permit(tmp_path):
    clock = FakeClock()
    active = manager(tmp_path, clock)
    begin(active)
    item = permit(active)
    gate = FinalSendGate(active)
    assert authorize(gate, item, config_version=2).reason_code == "CONFIG_VERSION_CHANGED"
    assert authorize(gate, item, risk_epoch=2).reason_code == "RISK_EPOCH_CHANGED"
    assert authorize(gate, item, price_usdt=Decimal("1.01")).reason_code == "ORDER_CHANGED"
    assert authorize(gate, item, reservation_active=False).reason_code == "RESERVATION_UNAVAILABLE"
    assert authorize(gate, item, safety_state="HALTED").reason_code == "SAFETY_BLOCKED"

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger


def D(value):
    return Decimal(str(value))


def test_execution_loss_survives_restart_and_new_session(tmp_path):
    path = tmp_path / "loss.json"
    at = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    ledger = LossBudgetLedger(path, campaign_id="life", campaign_limit_quote=D(10),
                              day_limit_quote=D(5), session_limit_quote=D(3))
    assert ledger.record("fill-1", D(2), session_id="s1", at_utc=at)
    recovered = LossBudgetLedger(path, campaign_id="life", campaign_limit_quote=D(10),
                                 day_limit_quote=D(5), session_limit_quote=D(3))
    assert recovered.record("fill-1", D(2), session_id="s1", at_utc=at) is False
    assert recovered.can_add_risk(session_id="s2", at_utc=at)
    assert recovered.record("fill-2", D(4), session_id="s2", at_utc=at)
    assert recovered.can_add_risk(session_id="s3", at_utc=at) is False
    assert recovered.status(session_id="s2", at_utc=at).day_loss_quote == D(6)


def test_day_rollover_does_not_reset_campaign_loss(tmp_path):
    at = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    ledger = LossBudgetLedger(tmp_path / "loss.json", campaign_id="life",
                              campaign_limit_quote=D(5), day_limit_quote=D(4),
                              session_limit_quote=D(4))
    ledger.record("a", D(3), session_id="s1", at_utc=at)
    ledger.record("b", D(2), session_id="s2", at_utc=at + timedelta(days=1))
    assert ledger.can_add_risk(session_id="s3", at_utc=at + timedelta(days=1)) is False


def test_deposit_and_starting_inventory_gain_cannot_offset_execution_loss(tmp_path):
    ledger = LossBudgetLedger(tmp_path / "loss.json", campaign_id="life",
                              campaign_limit_quote=D(1), day_limit_quote=D(1),
                              session_limit_quote=D(1))
    at = datetime(2026, 10, 5, tzinfo=timezone.utc)
    ledger.record("adverse-fill", D("1.1"), session_id="s1", at_utc=at)
    assert not ledger.can_add_risk(session_id="s2", at_utc=at)
    assert ledger.status(session_id="s2", at_utc=at).campaign_loss_quote == D("1.1")


def test_conflicting_duplicate_event_rejected(tmp_path):
    ledger = LossBudgetLedger(tmp_path / "loss.json", campaign_id="life",
                              campaign_limit_quote=D(5), day_limit_quote=D(5),
                              session_limit_quote=D(5))
    at = datetime(2026, 10, 5, tzinfo=timezone.utc)
    ledger.record("id", D(1), session_id="s1", at_utc=at)
    try:
        ledger.record("id", D(2), session_id="s1", at_utc=at)
    except ValueError as exc:
        assert str(exc) == "LOSS_EVENT_CONFLICT"
    else:
        assert False, "conflicting loss event must fail"

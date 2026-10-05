"""P4 service subsidy is reserved across session/day/campaign windows."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger

START = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def ledger(path):
    return SubsidyBudgetLedger(path, campaign_id="life-launch",
                               campaign_limit_quote=Decimal("10"), day_limit_quote=Decimal("4"),
                               session_limit_quote=Decimal("2"))


def test_restart_and_new_session_do_not_reset_day_or_campaign_budget(tmp_path):
    path = tmp_path / "subsidy.json"
    first = ledger(path)
    assert first.reserve("i1", Decimal("1.5"), session_id="s1", at_utc=START)
    restarted = ledger(path)
    assert not restarted.reserve("i2", Decimal("1"), session_id="s1", at_utc=START)
    assert restarted.reserve("i2", Decimal("1"), session_id="s2", at_utc=START)
    assert not restarted.reserve("i3", Decimal("2"), session_id="s2", at_utc=START)
    assert restarted.reserve("i3", Decimal("1"), session_id="s3", at_utc=START + timedelta(days=1))
    assert restarted.campaign_committed_quote == Decimal("3.5")


def test_actual_cost_reconciles_once_and_budget_cannot_be_overcommitted(tmp_path):
    book = ledger(tmp_path / "subsidy.json")
    assert book.reserve("i1", Decimal("1"), session_id="s1", at_utc=START)
    assert book.reconcile("i1", actual_cost_quote=Decimal("1.5"))
    assert not book.reconcile("i1", actual_cost_quote=Decimal("1.5"))
    assert not book.reserve("i2", Decimal("0.6"), session_id="s1", at_utc=START)
    assert book.reserve("i2", Decimal("0.5"), session_id="s1", at_utc=START)

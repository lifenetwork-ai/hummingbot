"""P4 service subsidy is reserved across session/day/campaign windows."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger

START = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def ledger(path):
    return SubsidyBudgetLedger(path, campaign_id="life-launch",
                               campaign_limit_quote=Decimal("10"), day_limit_quote=Decimal("4"),
                               session_limit_quote=Decimal("2"))


def test_restart_and_new_session_do_not_reset_day_or_campaign_budget(tmp_path):
    path = tmp_path / "subsidy.json"
    first = ledger(path)
    first.initialize_empty()
    assert first.reserve("i1", Decimal("1.5"), session_id="s1", at_utc=START)
    restarted = ledger(path)
    assert not restarted.reserve("i2", Decimal("1"), session_id="s1", at_utc=START)
    assert restarted.reserve("i2", Decimal("1"), session_id="s2", at_utc=START)
    assert not restarted.reserve("i3", Decimal("2"), session_id="s2", at_utc=START)
    assert restarted.reserve("i3", Decimal("1"), session_id="s3", at_utc=START + timedelta(days=1))
    assert restarted.campaign_committed_quote == Decimal("3.5")


def test_actual_cost_reconciles_once_and_budget_cannot_be_overcommitted(tmp_path):
    book = ledger(tmp_path / "subsidy.json")
    book.initialize_empty()
    assert book.reserve("i1", Decimal("1"), session_id="s1", at_utc=START)
    assert book.reconcile("i1", actual_cost_quote=Decimal("1.5"))
    assert not book.reconcile("i1", actual_cost_quote=Decimal("1.5"))
    assert not book.reserve("i2", Decimal("0.6"), session_id="s1", at_utc=START)
    assert book.reserve("i2", Decimal("0.5"), session_id="s1", at_utc=START)


def test_stale_writer_and_changed_policy_cannot_expand_durable_budget(tmp_path):
    path = tmp_path / "subsidy.json"
    first = ledger(path)
    first.initialize_empty()
    stale = ledger(path)
    assert first.reserve("i1", Decimal("1"), session_id="s1", at_utc=START)
    with pytest.raises(ValueError, match="SUBSIDY_JOURNAL_UNAVAILABLE"):
        stale.reserve("i2", Decimal("1"), session_id="s1", at_utc=START)
    assert ledger(path).campaign_committed_quote == Decimal("1")
    with pytest.raises(ValueError, match="SUBSIDY_BUDGET_POLICY_MISMATCH"):
        SubsidyBudgetLedger(path, campaign_id="life-launch",
                            campaign_limit_quote=Decimal("100"),
                            day_limit_quote=Decimal("40"),
                            session_limit_quote=Decimal("20"))


def test_missing_journal_and_ambiguous_commit_block_subsidy_reuse(tmp_path):
    path = tmp_path / "subsidy.json"
    book = ledger(path)
    book.initialize_empty()
    path.unlink()
    with pytest.raises(ValueError, match="SUBSIDY_JOURNAL_UNAVAILABLE"):
        book.reserve("i1", Decimal("0.5"), session_id="s1", at_utc=START)
    assert not path.exists()

    restored = ledger(path)
    restored.initialize_empty()
    real_save = restored._save

    def write_then_fail(entries):
        real_save(entries)
        raise OSError("directory sync ambiguous")

    with patch.object(restored, "_save", side_effect=write_then_fail):
        with pytest.raises(OSError):
            restored.reserve("i1", Decimal("0.5"), session_id="s1", at_utc=START)
    with pytest.raises(ValueError, match="SUBSIDY_JOURNAL_UNAVAILABLE"):
        restored.reserve("i2", Decimal("0.5"), session_id="s1", at_utc=START)
    assert ledger(path).campaign_committed_quote == Decimal("0.5")


def test_partial_fill_cost_above_hold_charges_budget_without_releasing_remainder(tmp_path):
    path = tmp_path / "subsidy.json"
    book = ledger(path)
    book.initialize_empty()
    assert book.reserve("i1", Decimal("0.1"), session_id="s1", at_utc=START)
    assert book.record_fill_floor("i1", Decimal("0.3"))
    assert not book.record_fill_floor("i1", Decimal("0.3"))
    assert book.verified_status(session_id="s1", at_utc=START).available_quote == Decimal("1.7")
    restored = ledger(path)
    assert restored.matches_fill_floor("i1", Decimal("0.3"))
    with pytest.raises(ValueError, match="SUBSIDY_FILL_COST_REGRESSION"):
        restored.record_fill_floor("i1", Decimal("0.2"))
    with pytest.raises(ValueError, match="SUBSIDY_RECONCILIATION_BELOW_FILLS"):
        restored.reconcile("i1", actual_cost_quote=Decimal("0.2"))

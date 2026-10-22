"""The review candidate must never silently become an offline completion."""
import json
from test.hummingbot.strategy_v2.life_liquidity import o8_acceptance as audit

import pytest


@pytest.fixture(autouse=True)
def complete_synthetic_candidate(tmp_path, monkeypatch):
    """Exercise current validator rules without claiming archived results.

    The archived input predates some required suites. Supply an explicitly
    open unit-test candidate in tmp_path; the real CLI still consumes the
    reviewed input and refuses its missing current evidence.
    """
    m = json.loads(audit.MANIFEST.read_text())
    for key, required in audit.REQUIRED.items():
        row = m['review_evidence'][key]
        for suffix in required:
            selector = audit.previous.PREFIX + suffix
            if selector not in row['tests']:
                row['tests'].append(selector)
                row['status'], row['remaining'] = 'open', ['O.8']
            case = row['cases'][0]
            if selector not in m['cases'][case]['tests']:
                m['cases'][case]['tests'].append(selector)
    path = tmp_path / 'synthetic_open_candidate.json'
    path.write_text(json.dumps(m))
    monkeypatch.setattr(audit, 'MANIFEST', path)


@pytest.mark.parametrize("fault", ["missing_review", "open_review", "missing_selector", "dropped_baseline", "budget", "plan"])
def test_o8_gate_rejects_incomplete_closure(fault):
    m = json.loads(audit.MANIFEST.read_text())
    for row in m["review_evidence"].values():
        row["status"] = "verified"
        row["remaining"] = []
    if fault == "missing_review":
        del m["review_evidence"]["O.8.1"]
    elif fault == "open_review":
        m["review_evidence"]["O.8.4"]["status"] = "open"
    elif fault == "missing_selector":
        m["review_evidence"]["O.8.5"]["tests"] = []
    elif fault == "dropped_baseline":
        m["cases"]["A01"]["tests"] = []
    elif fault == "budget":
        m["budgets_sha256"] = "0" * 64
    # The actual plan is still open. A manifest cannot override its checkboxes.
    with pytest.raises(ValueError):
        audit.validate_manifest(m)


def test_o8_candidate_is_explicitly_incomplete():
    m = json.loads(audit.MANIFEST.read_text())
    audit.validate_manifest(m, require_closed=False)
    assert any(row["status"] == "open" for row in m["review_evidence"].values())
    with pytest.raises(ValueError):
        audit.validate_manifest(m)


@pytest.mark.parametrize("row", ["O.8.3", "O.8.4"])
def test_hedge_wal_scope_cannot_be_dropped_from_acceptance(row):
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"][row]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_hedge_wal_scope.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_spot_journal_proof_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_spot_journal_proof.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_active_reservation_admission_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_active_reservation_admission.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_attribution_financial_proof_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_attribution_financial_proof.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_checkpoint_byte_proof_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_checkpoint_byte_proof.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_capital_permission_read_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_capital_permission_read.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_telemetry_reservation_lookup_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_telemetry_reservation_lookup.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_reservation_membership_cannot_be_dropped_from_acceptance():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_reservation_membership.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


@pytest.mark.parametrize("suite", ["sender_joint_admission", "capital_snapshot_scope", "joint_receipt_scope"])
def test_single_scope_admission_evidence_cannot_be_dropped(suite):
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_" + suite + ".py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_immutable_history_identity_evidence_cannot_be_dropped():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_immutable_history_identity.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)


def test_joint_capital_scope_evidence_cannot_be_dropped():
    m = json.loads(audit.MANIFEST.read_text())
    m["review_evidence"]["O.8.4"]["tests"].remove(
        "test/hummingbot/strategy_v2/life_liquidity/test_joint_capital_scope.py")
    with pytest.raises(ValueError):
        audit.validate_manifest(m, require_closed=False)

"""The review candidate must never silently become an offline completion."""
import json
from test.hummingbot.strategy_v2.life_liquidity import o8_acceptance as audit

import pytest


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

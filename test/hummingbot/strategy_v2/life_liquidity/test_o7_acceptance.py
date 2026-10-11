"""Full offline closure cannot reuse a spot-only or stale evidence map."""
import json
from test.hummingbot.strategy_v2.life_liquidity import o7_acceptance as audit

import pytest


def manifest():
    return json.loads(audit.MANIFEST.read_text())


@pytest.mark.parametrize("fault", ["spot_only", "open_extension", "missing_suite", "basket_passed", "stale_base", "missing_inventory", "scope_changed", "removed_spot"])
def test_full_gate_rejects_incomplete_or_overclaimed_manifest(fault):
    m = manifest()
    if fault == "spot_only":
        m["scope"] = "SPOT_OFFLINE_COMPLETE"
    elif fault == "open_extension":
        m["open_requirements"]["P6.1"]["remaining"].append("O.7")
    elif fault == "missing_suite":
        del m["extension_evidence"]["joint_recovery"]
    elif fault == "basket_passed":
        m["cases"]["A17"]["offline"] = "verified"
    elif fault == "stale_base":
        m["base_manifest_sha256"] = "0" * 64
    elif fault == "removed_spot":
        m["cases"]["A01"]["tests"] = []
    elif fault == "scope_changed":
        m["included_features"].append("portfolio_margin")
    else:
        m["open_requirements"]["P7.1"]["tests"] = []
    with pytest.raises(ValueError):
        audit.validate_manifest(m)


def test_complete_map_classifies_every_requirement_without_open_offline_work():
    m = manifest()
    audit.validate_manifest(m)
    assert len(m["cases"]) == 39 and len(m["open_requirements"]) == 118
    assert sum(c["offline"] == "verified" for c in m["cases"].values()) == 37
    assert all("O.7" not in r["remaining"] for r in m["open_requirements"].values())


def test_failed_extension_variant_cannot_hide_behind_spot_siblings():
    from test.hummingbot.strategy_v2.life_liquidity.o6_acceptance import TestResults, acceptance_results
    selector = "test/extension.py::test_combined"
    m = {"cases": {}, "open_requirements": {}, "extension_evidence": {"combined": {"tests": [selector]}}}
    r = TestResults()
    r.collected = [selector + "[good]", selector + "[bad]"]
    r.reports = {node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in r.collected}
    r.reports[r.collected[1]]["call"] = "skipped"
    with pytest.raises(ValueError, match="evidence"):
        acceptance_results(m, r)

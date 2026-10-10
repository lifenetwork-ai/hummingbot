"""O.6 cannot certify absent, skipped or unclassified acceptance evidence."""

import copy
import json
from test.hummingbot.strategy_v2.life_liquidity import o6_acceptance as audit

import pytest


def test_unknown_deferral_stage_cannot_close_spot_gate():
    manifest = json.loads(audit.MANIFEST.read_text())
    manifest["cases"]["A13"]["remaining"] = ["demo-later"]
    with pytest.raises(ValueError, match="stage"):
        audit.validate_manifest(manifest)


def test_new_unclassified_plan_requirement_prevents_closure(monkeypatch):
    manifest = json.loads(audit.MANIFEST.read_text())
    requirements = copy.deepcopy(audit.open_requirements())
    requirements["P5.99"] = {"parent": "P5.99", "sha256": "changed", "text": "New requirement"}
    monkeypatch.setattr(audit, "open_requirements", lambda: requirements)
    with pytest.raises(ValueError, match="Unclassified/stale"):
        audit.validate_manifest(manifest)


@pytest.mark.parametrize("fault", ["missing", "setup-skipped", "call-skipped", "teardown-failed", "missing-call"])
def test_every_collected_parameter_variant_must_finish_successfully(fault):
    selector = "test/example.py::test_case"
    nodes = [selector + "[good]", selector + "[bad]"]
    results = audit.TestResults()
    results.collected = nodes
    results.reports = {node: {"setup": "passed", "call": "passed", "teardown": "passed"} for node in nodes}
    if fault == "missing":
        results.collected = []
    elif fault == "missing-call":
        del results.reports[nodes[1]]["call"]
    else:
        phase, outcome = fault.split("-")
        results.reports[nodes[1]][phase] = outcome
    manifest = {"cases": {"A01": {"tests": [selector], "offline": "verified", "remaining": ["D"]}},
                "open_requirements": {}}
    with pytest.raises(ValueError, match="evidence"):
        audit.acceptance_results(manifest, results)


def test_deferred_requirement_cannot_cite_tests_that_never_executed():
    manifest = {"cases": {}, "open_requirements": {"P5.1": {"tests": ["test/missing.py"]}}}
    with pytest.raises(ValueError, match="Missing collected evidence: P5.1"):
        audit.acceptance_results(manifest, audit.TestResults())

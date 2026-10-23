"""O.6 cannot certify absent, skipped or unclassified acceptance evidence."""

import copy
import json
from test.hummingbot.strategy_v2.life_liquidity import o6_acceptance as audit

import pytest


def test_requirement_parent_cannot_leak_across_plan_sections(tmp_path, monkeypatch):
    plan = tmp_path / 'plan.md'
    plan.write_text('''## 6. Roadmap
- [ ] P9.13: Canary plan
  - [ ] Original child
### PostgreSQL environment qualification
- [ ] PG-E0: Select topology
  - [ ] Environment child
### Another product phase
- [x] P9.14: Implemented parent
  - [ ] Unfinished child
## 7. Required Acceptance Matrix
- [ ] A01: Not a P requirement
''')
    monkeypatch.setattr(audit, 'PLAN', plan)
    rows = audit.open_requirements()
    assert {r['text'] for r in rows.values()} == {
        'P9.13: Canary plan', 'Original child', 'Unfinished child'}
    assert {r['parent'] for r in rows.values()} == {'P9.13', 'P9.14'}


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

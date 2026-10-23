"""Re-run the declared O.6 spot gate; fake exchanges only, no credentials required.

Run from the repository root with the Hummingbot environment. The manifest is
reviewed evidence, not automatic proof that its descriptions match the tests.
"""

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.offline_inputs import SPOT_ACCEPTANCE

ROOT = Path(__file__).resolve().parents[4]

MANIFEST = SPOT_ACCEPTANCE
PLAN = ROOT / "docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def open_requirements():
    """Fingerprint every unchecked P item, including anonymous child requirements."""
    result = {}
    parent = None
    for line in PLAN.read_text().splitlines():
        if line.startswith("## 7."):
            break
        # A new section cannot inherit the previous product requirement.
        # PG environment gates have their own ownership and acceptance map.
        if re.match(r"^#{1,6} ", line):
            parent = None
        match = re.match(r"^- \[[ x]\] (P\d+\.\d+):", line)
        if match:
            parent = match[1]
        if not parent or not re.match(r"^(?:  )?- \[ \] ", line):
            continue
        text = line.split("] ", 1)[1]
        digest = hashlib.sha256(text.encode()).hexdigest()
        key = parent if match else f"{parent}#{digest[:12]}"
        result[key] = {"parent": parent, "sha256": digest, "text": text}
    return result


def validate_manifest(manifest):
    if set(manifest["cases"]) != {f"A{i:02}" for i in range(1, 40)}:
        raise ValueError("Acceptance matrix must classify exactly A01-A39")
    actual = open_requirements()
    classified = manifest["open_requirements"]
    if set(actual) != set(classified):
        raise ValueError(f"Unclassified/stale requirements: {set(actual) ^ set(classified)}")
    for key, item in actual.items():
        row = classified[key]
        if row["sha256"] != item["sha256"] or not row["owner"] or not row["reason"]:
            raise ValueError(f"Changed or unowned requirement: {key}")
        if not row["remaining"] or set(row["remaining"]) - {"O.7", "D", "R", "C"}:
            raise ValueError(f"Unresolved spot offline requirement: {key}")
    for key, row in manifest["cases"].items():
        if row["offline"] not in {"verified", "deferred"} or not row["owner"] or not row["reason"]:
            raise ValueError(f"Unclassified acceptance case: {key}")
        if row["offline"] == "verified" and not row["tests"]:
            raise ValueError(f"Missing executable evidence: {key}")
        if row["offline"] == "deferred" and not row["remaining"]:
            raise ValueError(f"Missing deferral stage: {key}")
        if set(row["remaining"]) - {"O.7", "D", "R", "C"}:
            raise ValueError(f"Unknown deferral stage: {key}")
    return actual


class TestResults:
    def __init__(self):
        self.collected = []
        self.reports = {}

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.setdefault(report.nodeid, {})[report.when] = report.outcome


def acceptance_results(manifest, results, *, include_extensions=False):
    evidence = {}
    rows = {**manifest["cases"], **manifest["open_requirements"], **manifest.get("extension_evidence", {})}
    for key, case in rows.items():
        nodes = set()
        for selector in case["tests"]:
            matches = {node for node in results.collected
                       if node == selector or node.startswith(selector + "::") or node.startswith(selector + "[")}
            if not matches:
                raise ValueError(f"Missing collected evidence: {key}: {selector}")
            nodes.update(matches)
        for node in nodes:
            if results.reports.get(node) != {"setup": "passed", "call": "passed", "teardown": "passed"}:
                raise ValueError(f"Incomplete, skipped or failed evidence: {key}: {node}")
        if key in manifest["cases"] or include_extensions and key in manifest.get("extension_evidence", {}):
            evidence[key] = {"offline": case.get("offline", "verified"), "passed_node_count": len(nodes),
                             "passed_nodeids_sha256": hashlib.sha256("\n".join(sorted(nodes)).encode()).hexdigest(),
                             "remaining": case.get("remaining", [])}
    return evidence


def run_audit(output, *, manifest_path=MANIFEST, scope="SPOT_OFFLINE_COMPLETE", validator=validate_manifest, entrypoint=None):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").unlink(missing_ok=True)
    if Path.cwd().resolve() != ROOT:
        raise SystemExit("Run this audit from the repository root")
    manifest = json.loads(manifest_path.read_text())
    validator(manifest)
    # Load binary extensions before tracing (local Python 3.13 first-import issue).
    import coverage
    import numpy  # noqa: F401
    import pandas  # noqa: F401
    import pytest

    results = TestResults()
    cov = coverage.Coverage(data_file=str(output / ".coverage"), source=["hummingbot", "controllers"])
    cov.start()
    exit_code = pytest.main(["-q", "--disable-warnings", *manifest["test_scopes"]], plugins=[results])
    cov.stop()
    cov.save()
    xml = output / "coverage.xml"
    cov.xml_report(outfile=str(xml), ignore_errors=False)
    (output / "tests.json").write_text(json.dumps(results.reports, indent=2) + "\n")
    if exit_code != 0 or len(results.collected) != len(results.reports):
        raise SystemExit("Regression failed or did not execute every collected test")
    if any(report != {"setup": "passed", "call": "passed", "teardown": "passed"}
           for report in results.reports.values()):
        raise SystemExit("Regression contains skipped, failed or incomplete tests")
    mapped = acceptance_results(manifest, results, include_extensions=True)
    cases = {key: row for key, row in mapped.items() if key in manifest["cases"]}
    from diff_cover.diff_cover_tool import main as diff_main
    coverage_results = {}
    for label, base in manifest["coverage_bases"].items():
        report = output / f"diff-{label}.json"
        code = diff_main(["diff-cover", str(xml), f"--compare-branch={base}", "--fail-under=80", "--format", f"json:{report}"])
        if code:
            raise SystemExit(f"Changed-line coverage failed: {label}")
        full = json.loads(report.read_text())
        coverage_results[label] = {
            "base_sha": base, "changed_executable_lines": full["total_num_lines"],
            "missed_lines": full["total_num_violations"],
            "percent_covered": 100 * (1 - full["total_num_violations"] / full["total_num_lines"]),
            "files": {file: {"percent_covered": stats["percent_covered"], "missed_lines": stats["violation_lines"]}
                      for file, stats in full["src_stats"].items()},
        }
    changed = git("diff", "--name-only", manifest["coverage_bases"]["feature"], "--", "hummingbot/**/*.py", "controllers/**/*.py").splitlines()
    measured = {Path(file).resolve().relative_to(ROOT).as_posix() for file in cov.get_data().measured_files()}
    missing = set(changed) - measured
    if missing:
        raise SystemExit(f"Changed source files omitted from coverage: {sorted(missing)}")
    summary = {
        "scope": scope, "production_permission": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_head": git("rev-parse", "HEAD"), "worktree_status": git("status", "--short"),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        "test_results_sha256": hashlib.sha256((output / "tests.json").read_bytes()).hexdigest(),
        "versions": {name: version(name) for name in ("pytest", "coverage", "diff-cover")},
        "runtime": {"python": sys.version, "platform": platform.platform()},
        "audit_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "entrypoint_sha256": hashlib.sha256(Path(entrypoint or __file__).read_bytes()).hexdigest(),
        "extension_evidence": {key: row for key, row in mapped.items() if key in manifest.get("extension_evidence", {})},
        "test_count": len(results.collected), "skipped": 0, "failed": 0,
        "case_counts": dict(Counter(row["offline"] for row in cases.values())),
        "cases": cases, "classified_open_requirements": len(manifest["open_requirements"]),
        "changed_source_files_measured": len(changed), "coverage": coverage_results,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"{scope} gate passed: {summary['test_count']} tests; production permission remains false.")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    run_audit(parser.parse_args().output_dir)


if __name__ == "__main__":
    main()

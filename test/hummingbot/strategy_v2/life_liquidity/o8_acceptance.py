"""Run review regressions with explicit closure; production permission stays false."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity import o7_acceptance as previous

base = previous.base
MANIFEST = base.ROOT / "docs/plans/evidence/life_o8_acceptance_manifest.json"
BUDGETS = base.ROOT / "docs/plans/evidence/life_o8_offline_budgets.json"
REQUIRED = {
    "O.8.1": ["test_o8_review.py::test_active_runner_poll_preserves_joint_authority",
              "test_o8_review.py::test_healthy_refresh_does_not_revoke_still_fresh_receipt",
              "test_o8_review.py::test_refresh_receipt_revocation",
              "test_o8_review.py::test_active_unarmed_spot_child_requires_a_captured_proposal"],
    "O.8.2": ["test_o8_review.py::test_spot_sell_fill_then_safety_tick_allows_bounded_open_hedge", "test_o8_continuous.py"],
    "O.8.3": ["test_o8_review.py::test_owned_pending_maker_does_not_pause_balanced_hedge",
              "test_o8_review.py::test_pending_maker_risk_and_identity_fail_closed", "test_hedge_wal_scope.py"],
    "O.8.4": ["test_o8_performance.py", "test_o8_settled_history.py", "test_o8_verified_state.py", "test_checkpoint_archive.py", "test_history_worker.py", "test_archived_journals.py", "test_lazy_journals.py", "test_hedge_wal_scope.py", "test_spot_journal_proof.py", "test_active_reservation_admission.py", "test_attribution_financial_proof.py", "test_o8_review.py::test_ten_thousand_terminal_reservations_restore_within_offline_budget",
              "test_o8_review.py::test_many_fills_qualification_restart_and_page_order",
              "test_o8_review.py::test_verified_ledger_cache_does_not_hide_corruption",
              "test_o8_review.py::test_action_cache_keeps_independent_verified_image"],
    "O.8.5": ["test_o8_verified_state.py::test_cancel_transports_start_before_full_joint_qualification",
              "test_o8_review.py::test_cancel_isolation_in_both_directions",
              "test_o8_review.py::test_stuck_swap_cancel_cannot_delay_spot_cancel",
              "test_o8_review.py::test_inflight_joint_collection_cannot_block_either_product_cancel"],
    "O.8.6": ["test_o8_review.py::test_same_millisecond_causal_close_open_history",
              "test_o8_review.py::test_many_fills_qualification_restart_and_page_order",
              "test_o8_review.py::test_tied_swap_history_release_and_cold_restart",
              "test_o8_review.py::test_same_direction_swap_ties_also_require_valid_causal_evidence"],
    "O.8.7": ["test_o8_continuous.py", "test_o8_acceptance.py"],
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_manifest(manifest, *, require_closed=True):
    previous.validate_manifest(manifest)
    old = json.loads(previous.MANIFEST.read_text())
    if (manifest.get("review_baseline_sha256") != sha(previous.MANIFEST)
            or manifest.get("budgets_sha256") != sha(BUDGETS)
            or set(manifest.get("review_evidence", {})) != set(REQUIRED)):
        raise ValueError("O.8 evidence/budget baseline mismatch")
    for group in ("cases", "open_requirements"):
        for key, row in old[group].items():
            if not set(row["tests"]) <= set(manifest[group][key]["tests"]):
                raise ValueError("O.7 baseline evidence cannot be removed")
    mapped = set(t for row in manifest["cases"].values() for t in row["tests"])
    for key, required in REQUIRED.items():
        row = manifest["review_evidence"][key]
        selectors = {previous.PREFIX + t for t in required}
        if (not selectors <= set(row["tests"]) or not selectors <= mapped
                or not row["owner"] or not row["requirements"] or not row["cases"]
                or row["status"] not in ("verified", "open")
                or require_closed and (row["status"] != "verified" or row["remaining"])):
            raise ValueError(f"O.8 unresolved/missing mandatory evidence: {key}")
    if require_closed:
        checked = set(re.findall(r"^\s*- \[x\] (O\.8(?:\.[1-7])?) —", base.PLAN.read_text(), re.M))
        if not {"O.8", *REQUIRED} <= checked:
            raise ValueError("O.8 plan closure is incomplete")
    return base.open_requirements()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--candidate", action="store_true", help="Run regression without claiming completion")
    args = parser.parse_args()
    scope = "OFFLINE_REVIEW_CANDIDATE" if args.candidate else "FULL_OFFLINE_COMPLETE"
    summary = base.run_audit(args.output_dir, manifest_path=MANIFEST, scope=scope,
                             validator=lambda m: validate_manifest(m, require_closed=not args.candidate), entrypoint=__file__)
    manifest = json.loads(MANIFEST.read_text())
    results = base.TestResults()
    results.reports = json.loads((args.output_dir / "tests.json").read_text())
    results.collected = list(results.reports)
    summary["review_evidence"] = base.acceptance_results(
        {"cases": {}, "open_requirements": {}, "extension_evidence": manifest["review_evidence"]},
        results, include_extensions=True)
    summary["unresolved_review"] = [k for k, r in manifest["review_evidence"].items() if r["status"] != "verified" or r["remaining"]]
    summary["offline_complete"] = not args.candidate and not summary["unresolved_review"]
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

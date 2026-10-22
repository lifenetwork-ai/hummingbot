"""Reproduce full declared offline LIFE scope; never demo/live eligibility."""
import argparse
import hashlib
import json
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity import o6_acceptance as base
from test.hummingbot.strategy_v2.life_liquidity.offline_inputs import FULL_ACCEPTANCE

MANIFEST = FULL_ACCEPTANCE
FEATURES = ["life_usdt_spot", "single_optional_benchmark", "linear_life_usdt_swap",
            "shared_capital", "bounded_hedge", "dynamic_carry", "joint_recovery", "bounded_inventory"]
SUITES = {
    "swap_send": "test_swap_executor.py", "swap_connector": "test_protected_swap_send.py",
    "shared_capital": "test_shared_capital.py", "shared_runner": "test_shared_capital_runner.py",
    "hedge": "test_hedge_coordinator.py", "carry": "test_carry_monitor.py", "carry_runner": "test_carry_runner.py",
    "joint_recovery": "test_joint_recovery.py", "inventory": "test_inventory_runner.py",
    "combined": "test_o7_combined_replay.py", "extension_telemetry": "test_extension_telemetry.py",
}
PREFIX = "test/hummingbot/strategy_v2/life_liquidity/"


def validate_manifest(manifest):
    base.validate_manifest(manifest)
    old = json.loads(base.MANIFEST.read_text())
    if (manifest.get("scope") != "FULL_OFFLINE_COMPLETE" or manifest.get("included_features") != FEATURES
            or manifest.get("base_manifest_sha256") != hashlib.sha256(base.MANIFEST.read_bytes()).hexdigest()
            or manifest["test_scopes"] != old["test_scopes"] or manifest["coverage_bases"] != old["coverage_bases"]
            or manifest.get("excluded_features") != ["benchmark_baskets", "cross_asset_hedges", "portfolio_margin", "multi_host_senders"]
            or set(manifest.get("extension_evidence", {})) != set(SUITES)):
        raise ValueError("Full offline scope/evidence baseline mismatch")
    for group in ("cases", "open_requirements"):
        for key, row in old[group].items():
            if not set(row["tests"]) <= set(manifest[group][key]["tests"]):
                raise ValueError("Spot baseline evidence cannot be removed")
    for key, filename in SUITES.items():
        if manifest["extension_evidence"][key].get("tests") != [PREFIX + filename]:
            raise ValueError("Missing required extension suite")
    for key, row in {**manifest["cases"], **manifest["open_requirements"]}.items():
        if "O.7" in row["remaining"]:
            raise ValueError("Unresolved offline extension requirement")
        if key.startswith(("P6.", "P7.")) and not row["tests"]:
            raise ValueError("Missing perpetual/inventory evidence")
    for key, row in manifest["cases"].items():
        expected = "deferred" if key in ("A13", "A17") else "verified"
        if row["offline"] != expected:
            raise ValueError("Overclaimed or missing offline case")
    if (manifest["cases"]["A17"].get("scope_exclusion") != "benchmark_baskets"
            or manifest["cases"]["A13"]["remaining"] != ["D", "R"]):
        raise ValueError("Exchange timer/basket scope classification invalid")
    return base.open_requirements()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    base.run_audit(parser.parse_args().output_dir, manifest_path=MANIFEST,
                   scope="FULL_OFFLINE_COMPLETE", validator=validate_manifest, entrypoint=__file__)


if __name__ == "__main__":
    main()

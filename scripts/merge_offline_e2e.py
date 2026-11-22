"""Verify that the offline CI shards exercised one image and the full scenario set."""
import argparse
import json
from pathlib import Path

from scripts.test_e2e_offline import all_scenarios, shard_scenarios

COMMON_EVIDENCE = (
    'image_id', 'registry_manifest_digest', 'architecture', 'image_size_bytes', 'image_size_budget_bytes',
    'config_sha256', 'fixture_sha256', 'harness_source_sha256',
)


def merge_shard_results(reports, *, shard_count, require_upgrade):
    names = all_scenarios(has_previous_image=require_upgrade)
    if not 1 <= shard_count <= len(names):
        raise ValueError('Invalid offline E2E shard count.')
    reports_by_index = {}
    for report in reports:
        index, count = report.get('scenario_shard', (0, 0))
        if count != shard_count or not 1 <= index <= count or index in reports_by_index:
            raise ValueError(f'Unexpected or duplicate offline E2E shard {index}/{count}.')
        reports_by_index[index] = report
    if set(reports_by_index) != set(range(1, shard_count + 1)):
        raise ValueError('Offline E2E is missing one or more shard reports.')

    reference = reports_by_index[1]
    evidence_fields = COMMON_EVIDENCE + ('predecessor_manifest_digest',) if require_upgrade else COMMON_EVIDENCE
    scenarios = {}
    for index, report in reports_by_index.items():
        if report.get('passed') is not True or not report.get('runtime_smoke'):
            raise ValueError(f'Offline E2E shard {index} or its runtime smoke check failed.')
        for field in evidence_fields:
            if not reference.get(field) or report.get(field) != reference[field]:
                raise ValueError(f'Offline E2E shard {index} differs in {field}.')
        expected = shard_scenarios(names, (index, shard_count))
        cases = report.get('scenarios', {})
        if set(cases) != set(expected) or any(case.get('passed') is not True for case in cases.values()):
            raise ValueError(f'Offline E2E shard {index} has missing, unexpected, or failed scenarios.')
        scenarios.update(cases)

    combined = {field: reference[field] for field in evidence_fields}
    return dict(combined, runtime_smoke=reference['runtime_smoke'], passed=True,
                release_scope_complete=require_upgrade, scenarios={name: scenarios[name] for name in names})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-count', type=int, required=True)
    parser.add_argument('--require-upgrade', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('reports', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        result = merge_shard_results([json.loads(path.read_text()) for path in args.reports],
                                     shard_count=args.shard_count, require_upgrade=args.require_upgrade)
    except (OSError, ValueError) as error:
        raise SystemExit(f'Offline E2E evidence check failed: {error}') from None
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(f"Verified {len(result['scenarios'])} scenarios across {args.shard_count} offline E2E shards.")


if __name__ == '__main__':
    main()

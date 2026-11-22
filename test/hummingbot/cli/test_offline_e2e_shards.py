from collections import Counter

import pytest

from scripts import test_e2e_offline as runner
from scripts.merge_offline_e2e import merge_shard_results


@pytest.fixture(params=[False, True], ids=['current-only', 'with-predecessor'])
def reports(request):
    has_previous_image = request.param
    names = runner.all_scenarios(has_previous_image=has_previous_image)
    return has_previous_image, [
        dict(scenario_shard=[index, 3], image_id='image', registry_manifest_digest='manifest',
             architecture='amd64', image_size_bytes=1, image_size_budget_bytes=2,
             config_sha256='config', fixture_sha256={'fixtures.json': 'fixtures'},
             harness_source_sha256={'test_e2e_offline.py': 'runner'}, runtime_smoke={'sdk': True},
             predecessor_manifest_digest='prev' if has_previous_image else None,
             passed=True, release_scope_complete=False,
             scenarios={name: {'passed': True} for name in runner.shard_scenarios(names, (index, 3))})
        for index in range(1, 4)
    ]


def test_ci_shards_cover_each_scenario_once_and_merge_in_suite_order(reports):
    has_previous_image, shards = reports
    names = runner.all_scenarios(has_previous_image=has_previous_image)
    counts = Counter(name for shard in shards for name in shard['scenarios'])
    assert len(names) >= 43
    assert len(counts) == len(names)
    assert counts == Counter({name: 1 for name in names})
    assert ('upgrade' in counts) is has_previous_image
    result = merge_shard_results(list(reversed(shards)), shard_count=3, require_upgrade=has_previous_image)
    assert result['passed'] is True
    assert result['release_scope_complete'] is has_previous_image
    assert tuple(result['scenarios']) == names
    assert 'scenario_shard' not in result


@pytest.mark.parametrize('inconsistent', [
    'missing-shard', 'duplicate-shard', 'missing-case', 'failed-case',
    'different-image', 'different-manifest', 'different-fixture',
])
def test_ci_gate_rejects_incomplete_or_inconsistent_evidence(reports, inconsistent):
    require_upgrade, shards = reports
    if inconsistent == 'missing-shard':
        shards.pop()
    elif inconsistent == 'duplicate-shard':
        shards[1] = shards[0]
    elif inconsistent == 'missing-case':
        shards[0]['scenarios'].popitem()
    elif inconsistent == 'failed-case':
        next(iter(shards[0]['scenarios'].values()))['passed'] = False
    elif inconsistent == 'different-image':
        shards[1]['image_id'] = 'other'
    elif inconsistent == 'different-manifest':
        shards[1]['registry_manifest_digest'] = 'other'
    elif inconsistent == 'different-fixture':
        shards[1]['fixture_sha256'] = {'fixtures.json': 'other'}
    with pytest.raises(ValueError):
        merge_shard_results(shards, shard_count=3, require_upgrade=require_upgrade)


def test_ci_gate_requires_upgrade_when_predecessor_is_configured(reports):
    has_previous_image, shards = reports
    for shard in shards:
        shard['scenarios'].pop('upgrade', None)
    failure = 'missing, unexpected, or failed scenarios' if has_previous_image else 'predecessor_manifest_digest'
    with pytest.raises(ValueError, match=failure):
        merge_shard_results(shards, shard_count=3, require_upgrade=True)

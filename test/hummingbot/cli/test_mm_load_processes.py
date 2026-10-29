"""Multi-process measurements must not hide an API or count shared pages twice."""
from copy import deepcopy

from scripts.mm_load_processes import merge_metrics


def metric(role, at=100):
    return dict(process_role=role, at=at, memory={'VmRSS': 100, 'VmHWM': 150},
                distributions={'pool_checkout_ms': dict(count=10, p50=1, p95=2, p99=3, max=4)},
                totals={'sql_statements': 10}, latest={}, route_queries={}, checked_out=1, pool_size=3)


def test_metrics_require_both_roles_and_use_explicit_conservative_quantiles():
    engine, api = metric('engine'), metric('api')
    engine.update(container_memory={'current_bytes': 160, 'peak_bytes': 220}, runtime_phase='running')
    api['distributions']['pool_checkout_ms'].update(p95=12, p99=20)
    api['pool_size'] = 2
    original = deepcopy(engine)
    assert merge_metrics(engine, None) is None
    result = merge_metrics(engine, api)
    assert result['memory'] == {'VmRSS': 160, 'VmHWM': 220}
    assert result['pool_size'] == 5 and result['checked_out'] == 2
    assert result['totals']['sql_statements'] == 20
    assert result['distributions']['pool_checkout_ms']['p95'] == 12
    assert result['distributions']['pool_checkout_ms']['count'] == 20
    assert result['distribution_aggregation'] == 'maximum_per_role_quantile_not_pooled_percentile'
    assert result['role_metrics']['engine']['memory']['VmRSS'] == 100
    assert engine == original


def test_metrics_cannot_borrow_stale_api_or_unrelated_role():
    engine = metric('engine')
    engine['container_memory'] = dict(current_bytes=160, peak_bytes=220)
    assert merge_metrics(engine, metric('api', at=70)) is None
    assert merge_metrics(engine, metric('engine')) is None
    legacy = metric('combined')
    assert merge_metrics(legacy, None) == legacy


def test_two_frozen_metric_files_cannot_certify_a_live_window():
    engine = metric('engine')
    engine['container_memory'] = dict(current_bytes=160, peak_bytes=220)
    assert merge_metrics(engine, metric('api'), now=110) is not None
    assert merge_metrics(engine, metric('api'), now=130) is None
    assert merge_metrics(engine, metric('api'), now=90) is None


def test_process_snapshot_uses_only_managed_roles_and_converts_rss_bytes(tmp_path):
    from test.e2e.offline.load_process_metrics import process_snapshot
    for pid in ('1', '10', '11', '12'):
        path = tmp_path / pid
        (path / 'fd').mkdir(parents=True)
        (path / 'fd/0').touch()
        (path / 'status').write_text('VmRSS:\t12 kB\nVmHWM:\t20 kB\nThreads:\t3\n')
        (path / 'cmdline').write_bytes(b'python\0-c\0ignored')
    (tmp_path / '1/task/1').mkdir(parents=True)
    (tmp_path / '1/task/1/children').write_text('10 11 12 99')
    (tmp_path / '10/cmdline').write_bytes(b'python\0-m\0hummingbot.cli.service\0--role\0engine\0')
    (tmp_path / '11/cmdline').write_bytes(b'python\0/test-assets/load_entrypoint.py\0--role\0api\0')
    result = process_snapshot(tmp_path)
    assert set(result) == {'supervisor', 'engine', 'api'}
    assert result['api'] == dict(pid=11, VmRSS=12 * 1024, VmHWM=20 * 1024, Threads=3, fds=1)

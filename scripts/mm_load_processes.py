"""Conservative multi-role aggregation for the offline load harness only."""
from copy import deepcopy


def merge_metrics(engine, api, *, now=None):
    if engine is None or engine.get('process_role') != 'engine':
        return engine
    if (api is None or api.get('process_role') != 'api'
            or abs(engine['at'] - api['at']) > 15 or not engine.get('container_memory')):
        return None
    if now is not None and any(not 0 <= now - row['at'] <= 15 for row in (engine, api)):
        return None
    expected_api = engine.get('processes', {}).get('api', {}).get('pid')
    if expected_api is not None and api.get('process_id') != expected_api:
        return None
    result = deepcopy(engine)
    result['role_metrics'] = {'engine': engine, 'api': api}
    # cgroup includes parent/children and page cache. It is conservative versus
    # RSS; adding per-process RSS would double-count shared libraries/pages.
    memory = engine['container_memory']
    result['memory'] = dict(VmRSS=memory['current_bytes'], VmHWM=memory['peak_bytes'])
    result['memory_measurement'] = 'cgroup_current_and_peak_including_page_cache; per_role_RSS_separate'
    result['distribution_aggregation'] = 'maximum_per_role_quantile_not_pooled_percentile'
    for name, value in api['distributions'].items():
        previous = result['distributions'].get(name)
        if previous is None:
            result['distributions'][name] = deepcopy(value)
        else:
            combined = dict(count=previous['count'] + value['count'])
            for key in ('p50', 'p95', 'p99', 'max'):
                values = [v for v in (previous[key], value[key]) if v is not None]
                combined[key] = max(values) if values else None
            result['distributions'][name] = combined
    for name, value in api['totals'].items():
        result['totals'][name] = result['totals'].get(name, 0) + value
    for name, value in api['latest'].items():
        result['latest'].setdefault(name, value)
    for name, value in api['route_queries'].items():
        previous = result['route_queries'].get(name)
        if previous is None:
            result['route_queries'][name] = deepcopy(value)
        else:
            previous.update(calls=previous['calls'] + value['calls'], queries=previous['queries'] + value['queries'],
                            min=min(v for v in (previous['min'], value['min']) if v is not None),
                            max=max(previous['max'], value['max']))
    result['checked_out'] = (engine.get('checked_out') or 0) + (api.get('checked_out') or 0)
    result['pool_size'] = (engine.get('pool_size') or 0) + (api.get('pool_size') or 0)
    return result

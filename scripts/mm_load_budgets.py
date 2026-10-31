"""Local load acceptance budgets, declared before collecting measurements."""
import math
from statistics import median


def percentile(values, fraction):
    if not values:
        return None
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def violations(result, *, require_rates=True):
    checks = {"read_rate": result.get("completed_reads", 0) >= result.get("requested_reads", 1),
              "generator_backlog": result.get("queued_reads_at_generation_end", float("inf")) <= 10,
              "runtime_halt": result.get("runtime", {}).get("runtime_phase") not in (None, "halted"),
              "errors": result.get("unexpected_errors", 1) == 0,
              "rss": result.get("peak_rss_bytes", float("inf")) <= 1.5 * 1024**3,
              "backlog": result.get("backlog_drain_seconds", float("inf")) <= 10}
    if "auxiliary" in result:
        extra = result["auxiliary"]
        checks["auxiliary_reads"] = extra.get("completed_reads", 0) >= extra.get("requested_reads", 1) > 0
        for key, maximum in (("read_p95_ms", 500), ("read_p99_ms", 1000)):
            value = extra.get(key)
            checks["auxiliary_" + key.removesuffix("_ms")] = value is not None and math.isfinite(value) and value <= maximum
    if require_rates:
        checks["market_rate"] = result.get("market_updates_per_second", 0) + 4 / result["duration_seconds"] >= 100
        checks["fill_rate"] = result.get("fills_per_second", 0) + 1 / result["duration_seconds"] >= 1
    for key, maximum in (("read_p95_ms", 500), ("read_p99_ms", 1000)):
        value = result.get(key)
        checks[key.removesuffix("_ms")] = value is not None and math.isfinite(value) and value <= maximum
    for action, limit in (("start", 15), ("switch", 15), ("stop", 5)):
        checks["command_" + action] = result.get("command_seconds", {}).get(action, float("inf")) <= limit
    for key, limit in (("command_accept_p95_ms", 500), ("command_accept_p99_ms", 1000)):
        value = result.get(key)
        checks[key] = value is not None and math.isfinite(value) and value <= limit
    return [name for name, passed in checks.items() if not passed]


def memory_retention(samples):
    """Bound retained memory separately from reclaimable disk cache; keep tmpfs.

    Historical samples without a breakdown retain their original conservative
    cgroup-current rule. They must never be reclassified using guessed cache.
    The absolute container peak and per-role RSS checks remain independent.
    """
    breakdown = any('file_bytes' in row.get('container_memory', {}) or
                    'shmem_bytes' in row.get('container_memory', {}) for row in samples)
    values = []
    for row in samples:
        current = row['memory']['VmRSS']
        if not isinstance(current, int) or isinstance(current, bool) or current < 0:
            raise ValueError('Invalid memory sample')
        if breakdown:
            fields = row.get('container_memory', {})
            if any(not isinstance(fields.get(key), int) or isinstance(fields[key], bool) or fields[key] < 0
                   for key in ('current_bytes', 'file_bytes', 'shmem_bytes')):
                raise ValueError('Incomplete memory breakdown')
            if fields['current_bytes'] != current or not fields['shmem_bytes'] <= fields['file_bytes'] <= current:
                raise ValueError('Inconsistent memory breakdown')
            current -= fields['file_bytes'] - fields['shmem_bytes']
        values.append(current)
    early, late = median(values[:5]), median(values[-5:])
    return dict(basis='cgroup_current_minus_file_plus_shmem' if breakdown else 'legacy_cgroup_current',
                early_median_bytes=early, late_median_bytes=late, growth_bytes=late - early,
                budget_bytes=64 * 1024**2,
                raw_cgroup_growth_bytes=median(row['memory']['VmRSS'] for row in samples[-5:]) -
                median(row['memory']['VmRSS'] for row in samples[:5]))


def soak_violations(samples):
    if len(samples) < 55:
        return ["missing_minute_samples"]
    first, last = samples[:5], samples[-5:]
    times = [row.get("at") for row in samples]
    continuous = all(isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp)
                     for stamp in times)
    if continuous:
        # A suspended host can eventually collect enough active-time samples.
        # Their wall-clock cadence must also prove one continuous hour.
        continuous = all(0 < right - left <= 90 for left, right in zip(times, times[1:]))
    try:
        retained = memory_retention(samples)
    except (KeyError, TypeError, ValueError):
        retained = None
    checks = {
        "sampling_continuity": continuous,
        "runtime_halt": not any(row.get("runtime_phase") == "halted" for row in samples),
        "memory_breakdown_missing": retained is not None,
        "rss_growth": retained is not None and retained['growth_bytes'] <= retained['budget_bytes'],
        "book_queue_growth": median(row["latest"].get("book_backlog", 0) for row in last) <= max(10, median(row["latest"].get("book_backlog", 0) for row in first) + 4),
        "pool_wait_growth": median(row["distributions"]["pool_checkout_ms"]["p95"] for row in last) <= max(20, median(row["distributions"]["pool_checkout_ms"]["p95"] for row in first) + 5),
    }
    if any('processes' in row for row in samples):
        roles = ('supervisor', 'engine', 'api')
        complete = all(all(role in row.get('processes', {}) and
                           all(isinstance(row['processes'][role].get(key), int) for key in ('pid', 'VmRSS', 'Threads', 'fds'))
                           for role in roles) for row in samples)
        checks['process_missing'] = complete
        if complete:
            checks['process_pid'] = all(len({row['processes'][role]['pid'] for row in samples}) == 1 for role in roles)
            for key, limit, label in (('VmRSS', 64 * 1024**2, 'rss'), ('Threads', 4, 'threads'), ('fds', 8, 'fds')):
                checks['process_' + label] = all(
                    median(row['processes'][role][key] for row in last) <=
                    median(row['processes'][role][key] for row in first) + limit for role in roles)
    return [name for name, passed in checks.items() if not passed]

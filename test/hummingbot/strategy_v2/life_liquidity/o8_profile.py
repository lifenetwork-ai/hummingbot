"""Offline diagnostics for the unchanged settled-history acceptance workload.

Run each invocation in a fresh process. ``timing`` records stage clocks only;
``profile`` adds main-thread cProfile and counted file reads/fsync/GC pauses.
Profile timings are diagnostic, never acceptance evidence or worker deadlines.
No credentials, network, controller defaults or production permission are used.
"""
import argparse
import asyncio
import cProfile
import gc
import hashlib
import json
import os
import platform
import pstats
import resource
import subprocess
import sys
import tempfile
import time
import traceback
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
TARGETS = {
    "verify_maps", "read_limited", "read", "verify", "verified_copy", "assert_verified",
    "verified_disk", "verify_memory", "all_records", "_readiness_cut", "ready", "_commit",
    "_save", "_payload", "_load_archive_data", "_split", "prepare", "_prepare_history",
    "_prime_reservations", "_capture", "_image_for", "_durable_sources_match", "capital",
}


def source_snapshot():
    paths = [*ROOT.glob("hummingbot/strategy_v2/life_liquidity/*.py"),
             ROOT / "controllers/generic/life_liquidity.py",
             ROOT / "hummingbot/strategy/strategy_v2_base.py",
             ROOT / "hummingbot/strategy_v2/executors/order_executor/order_executor.py",
             *Path(__file__).parent.glob("*.py")]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


class StageRecorder:
    def __init__(self, profile=False):
        self.profile = profile
        self.current = None
        self.stages = []
        self._gc_started = None

    @contextmanager
    def stage(self, name):
        if self.current is not None:
            raise ValueError("diagnostic stages must not nest")
        row = dict(name=name, reads=defaultdict(lambda: [0, 0, 0.0]), fsync_calls=0,
                   fsync_ms=0.0, gc_collections=0, gc_ms=0.0)
        profiler = cProfile.Profile() if self.profile and name != "seed_and_migrate" else None
        self.current = row
        self._gc_started = None
        wall, cpu = time.perf_counter(), time.process_time()
        if profiler is not None:
            profiler.enable()
        try:
            yield
        finally:
            if profiler is not None:
                profiler.disable()
            row.update(wall_ms=(time.perf_counter() - wall) * 1000,
                       process_cpu_ms=(time.process_time() - cpu) * 1000, profiler=profiler)
            self.current = None
            self._gc_started = None
            self.stages.append(row)

    def _read(self, callback, path, *args, **kwargs):
        row = self.current
        started = time.perf_counter()
        value = callback(path, *args, **kwargs)
        if row is not None:
            # Paths omit the temporary root, retaining journal/segment identity.
            path = Path(path)
            key = path.parent.name + "/" + path.name if path.parent.name.endswith(".segments") else path.name
            count = row["reads"][key]
            count[0] += 1
            count[1] += len(value)
            count[2] += (time.perf_counter() - started) * 1000
        return value

    @contextmanager
    def instruments(self, monkeypatch):
        if not self.profile:
            yield
            return
        from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import read_limited
        original_read, original_fsync = Path.read_bytes, os.fsync

        def counted_read(path):
            return self._read(original_read, path)

        def counted_limited(path, maximum):
            return self._read(read_limited, path, maximum)

        def counted_fsync(fd):
            started, row = time.perf_counter(), self.current
            try:
                return original_fsync(fd)
            finally:
                if row is not None:
                    row["fsync_calls"] += 1
                    row["fsync_ms"] += (time.perf_counter() - started) * 1000

        def gc_event(phase, info):
            row = self.current
            if row is None:
                return
            if phase == "start":
                self._gc_started = (row, time.perf_counter())
            elif self._gc_started is not None:
                owner, started = self._gc_started
                if owner is row:
                    row["gc_collections"] += 1
                    row["gc_ms"] += (time.perf_counter() - started) * 1000
                self._gc_started = None

        # Imports alias read_limited; patch each loaded alias, never file semantics.
        for module_name, module in list(sys.modules.items()):
            if module_name.startswith("hummingbot.strategy_v2.life_liquidity."):
                if getattr(module, "read_limited", None) is read_limited:
                    monkeypatch.setattr(module, "read_limited", counted_limited)
        monkeypatch.setattr(Path, "read_bytes", counted_read)
        monkeypatch.setattr(os, "fsync", counted_fsync)
        gc.callbacks.append(gc_event)
        try:
            yield
        finally:
            gc.callbacks.remove(gc_event)

    @staticmethod
    def _function(key, values):
        filename, line, name = key
        path = Path(filename)
        if path.is_absolute() and path.is_relative_to(ROOT):
            filename = str(path.relative_to(ROOT))
        primitive, calls, own, cumulative, _ = values
        return dict(file=filename, line=line, function=name, primitive_calls=primitive,
                    calls=calls, self_ms=own * 1000, cumulative_ms=cumulative * 1000)

    def export(self, directory):
        rows = []
        for index, original in enumerate(self.stages):
            row = dict(original)
            reads, profiler = row.pop("reads"), row.pop("profiler")
            row["reads"] = dict(successful_calls=sum(v[0] for v in reads.values()),
                                returned_bytes=sum(v[1] for v in reads.values()),
                                wall_ms=sum(v[2] for v in reads.values()), distinct_paths=len(reads),
                                top_paths=[dict(path=k, calls=v[0], returned_bytes=v[1], wall_ms=v[2])
                                           for k, v in sorted(reads.items(), key=lambda kv: kv[1][1], reverse=True)[:15]])
            if profiler is not None:
                filename = f"{index:02}-{row['name']}.prof"
                profiler.dump_stats(str(directory / filename))
                stats = pstats.Stats(profiler).stats
                row["raw_profile"] = filename
                row["top_self"] = [self._function(k, v) for k, v in
                                   sorted(stats.items(), key=lambda kv: kv[1][2], reverse=True)[:25]]
                row["top_cumulative"] = [self._function(k, v) for k, v in
                                         sorted(stats.items(), key=lambda kv: kv[1][3], reverse=True)[:25]]
                row["target_functions"] = [self._function(k, v) for k, v in stats.items()
                                           if k[2] in TARGETS and "life_liquidity" in k[0]]
            rows.append(row)
        return rows


async def run(history, variant, recorder, properties):
    from test.hummingbot.strategy_v2.life_liquidity.test_o8_settled_history import exercise_settled_history
    archived = {"legacy": False, "segmented": True, "journals": "journals",
                "thread": "worker", "process": "process"}[variant]
    with tempfile.TemporaryDirectory(prefix="life-o8-profile-") as temporary, pytest.MonkeyPatch.context() as patch:
        with recorder.instruments(patch):
            await exercise_settled_history(Path(temporary), history, archived,
                                           lambda key, value: properties.update({key: json.loads(value)}),
                                           patch, observe=recorder.stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=int, choices=(0, 1000, 10000), required=True)
    parser.add_argument("--variant", choices=("legacy", "segmented", "journals", "thread", "process"), default="journals")
    parser.add_argument("--mode", choices=("timing", "profile"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    recorder, properties = StageRecorder(args.mode == "profile"), {}
    before, status, error = source_snapshot(), "passed", None
    try:
        asyncio.run(run(args.history, args.variant, recorder, properties))
    except (Exception, pytest.fail.Exception) as exc:
        status = "failed"
        error = dict(type=type(exc).__name__, message=str(exc), traceback=traceback.format_exc())
    after = source_snapshot()
    report = dict(schema_version=1, diagnostic_only=True, offline_complete=False, production_order_permission=False,
                  mode=args.mode, history=args.history, variant=args.variant, workload_status=status, error=error,
                  git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  source_unchanged=before == after, source_sha256=before,
                  budget_sha256=hashlib.sha256((Path(__file__).with_name("fixtures") / "offline_inputs/budgets.json").read_bytes()).hexdigest(),
                  python=sys.version, platform=platform.platform(), pid=os.getpid(),
                  process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
                  measurement_notes=["Fixture construction excluded from hot-path profiles; seed/migration clocks reported separately.",
                                     "Timing mode has stage clocks only; profile mode adds cProfile/I/O/GC overhead.",
                                     "Async stages include other tasks running on the main event-loop thread during awaits.",
                                     "Cumulative function times overlap; do not sum them. Coroutine calls include resumptions.",
                                     "Reads count successful Path.read_bytes/read_limited returned bytes, not physical disk I/O.",
                                     "RSS is the process lifetime high-water mark, including fixture setup; not active-set RAM.",
                                     "The original workload assertions and all timing/worker deadlines remain unchanged."],
                  workload=properties, stages=recorder.export(args.output_dir))
    (args.output_dir / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(dict(status=status, history=args.history, variant=args.variant, mode=args.mode,
                          output=str(args.output_dir / "result.json"))))
    return 0 if status == "passed" and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())

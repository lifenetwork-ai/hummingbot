"""Optional bounded loop/GC diagnostic, reusing the existing trace writer."""
import asyncio
import gc
import json
import os
import sys
import threading
import time
from pathlib import Path

from load_trace import FullGCProbe, TraceSink, stack_snapshot


def attach(app):
    directory = Path(os.environ['HBOT_LOAD_TRACE_DIRECTORY'])
    sink = TraceSink(directory / f'api-loop-{os.getpid()}.jsonl')
    full_gc = FullGCProbe()
    stopping = threading.Event()
    beat = time.monotonic_ns()
    gc_started = {}
    task = worker = None

    def record_gc(phase, info):
        full_gc(phase, info)
        key = threading.get_ident(), info['generation']
        if phase == 'start':
            gc_started[key] = time.monotonic_ns()
        elif key in gc_started:
            started = gc_started.pop(key)
            duration = (time.monotonic_ns() - started) / 1e6
            if duration >= 10:
                sink.emit(dict(kind='gc', at=time.time(), start_ns=started, duration_ms=duration,
                               thread_id=key[0], generation=key[1], collected=info['collected']))

    def watchdog(loop_thread):
        previous = 0
        while not stopping.wait(.1):
            now = time.monotonic_ns()
            if now - beat < 200000000 or now - previous < 100000000:
                continue
            previous = now
            # Retain only frame summaries; raw frames can keep request objects alive.
            stacks = {str(tid): stack_snapshot(frame) for tid, frame in sys._current_frames().items()}
            cpu = Path('/sys/fs/cgroup/cpu.stat')
            sink.emit(dict(kind='api_loop_stall', at=time.time(), at_ns=now,
                           last_beat_ns=beat, loop_thread_id=loop_thread,
                           lag_ms=(now - beat) / 1e6,
                           stacks=stacks,
                           cgroup_cpu=cpu.read_text() if cpu.is_file() else None))

    async def heartbeat():
        nonlocal beat
        while True:
            await asyncio.sleep(.05)
            beat = time.monotonic_ns()

    async def start(app):
        nonlocal task, worker
        task = asyncio.create_task(heartbeat())
        gc.callbacks.append(record_gc)
        worker = threading.Thread(target=watchdog, args=(threading.get_ident(),), daemon=True)
        worker.start()

    async def close(app):
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        stopping.set()
        if worker is not None:
            worker.join(timeout=1)
        if record_gc in gc.callbacks:
            gc.callbacks.remove(record_gc)
        sink.close()
        (directory / f'api-loop-{os.getpid()}-summary.json').write_text(json.dumps(
            dict(sink=sink.summary(), full_gc=list(full_gc.events)), indent=2))

    app.on_startup.append(start)
    app.on_cleanup.append(close)

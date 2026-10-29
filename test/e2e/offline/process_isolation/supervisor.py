"""Experimental single-container two-process lifecycle. No in-place restarts."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get('ARCH_EXPERIMENT_SUPERVISION') == 'restart':
    from restarted import run
    sys.exit(run())

children = []
stopping = False


def stop(*args):
    global stopping
    stopping = True
    for child in children:
        if child.poll() is None:
            child.terminate()


for signum in (signal.SIGTERM, signal.SIGINT):
    signal.signal(signum, stop)
try:
    engine_env = {**os.environ, 'PORT': '8081', 'ADMIN_API_ENABLED': 'false', 'ARCH_EXPERIMENT_SPLIT': '1'}
    children.append(subprocess.Popen([sys.executable, '/test-assets/engine.py'], env=engine_env))
    # API holds administrative/database access, but never the exchange keys.
    api_env = {k: v for k, v in os.environ.items() if not k.startswith('HBOT_OKX')}
    children.append(subprocess.Popen([sys.executable, '/test-assets/admin.py'], env=api_env))
    Path('/tmp/architecture-children.json').write_text(json.dumps([c.pid for c in children]))
    while not stopping and all(child.poll() is None for child in children):
        time.sleep(.05)
    failed = next((child.returncode for child in children if child.returncode is not None), None)
    code = 0 if stopping else (failed if failed not in (None, 0) else 1)
    if not stopping:
        print(json.dumps({'severity': 'ERROR', 'event': 'critical_child_exited', 'exit_code': code}), flush=True)
finally:
    stop()
    deadline = time.monotonic() + 8.5
    for child in children:
        try:
            child.wait(timeout=max(.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=.4)
            code = 1
sys.exit(code)

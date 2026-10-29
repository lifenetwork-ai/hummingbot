"""Experimental independent child restart; uses existing engine recovery only."""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from lifecycle import ChildSupervisor, HealthWatchdog


def responding(role):
    port = 8081 if role == 'engine' else 8080
    try:
        with urlopen(f'http://127.0.0.1:{port}/live', timeout=.3) as response:
            return response.status == 200
    except HTTPError as error:
        # An API can legitimately initialize while its engine is unavailable.
        try:
            return role == 'api' and error.code == 503
        finally:
            error.close()
    except (OSError, URLError, TimeoutError):
        return False


def run():
    def emit(event):
        print(json.dumps(event), flush=True)

    def launch(role):
        if role == 'engine':
            env = {**os.environ, 'PORT': '8081', 'ADMIN_API_ENABLED': 'false', 'ARCH_EXPERIMENT_SPLIT': '1'}
        else:
            env = {k: v for k, v in os.environ.items() if not k.startswith('HBOT_OKX')}
        entrypoint = 'engine.py' if role == 'engine' else 'admin.py'
        return subprocess.Popen([sys.executable, f'/test-assets/{entrypoint}'], env=env)

    owner = ChildSupervisor(launch, emit=emit)
    watchdog = HealthWatchdog(owner)

    def stopping(*args):
        owner.stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stopping)
    previous_pids, next_probe = None, 0
    try:
        while not owner.stopping:
            owner.tick()
            pids = [owner.children[role].pid if owner.children[role] is not None else None for role in owner.ROLES]
            if pids != previous_pids:
                temporary = Path('/tmp/architecture-children.json.tmp')
                temporary.write_text(json.dumps(pids))
                temporary.replace('/tmp/architecture-children.json')
                previous_pids = pids
            if time.monotonic() >= next_probe:
                for role in owner.ROLES:
                    watchdog.observe(role, responding(role))
                next_probe = time.monotonic() + 1
            time.sleep(.05)
        return 0
    finally:
        owner.shutdown()

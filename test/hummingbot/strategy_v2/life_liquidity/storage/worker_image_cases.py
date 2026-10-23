"""Actual immutable artifact/process qualification; use run_worker_image.py.

The injected test launcher owns no exchange or database. These cases qualify
the image/foreground boundary, not financial crash recovery or live permission.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from time import monotonic
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[5]


async def docker(*args, timeout=30, check=True):
    process = await asyncio.create_subprocess_exec('docker', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, error = await asyncio.wait_for(process.communicate(), timeout)
        if check:
            assert process.returncode == 0, error.decode(errors='replace')[-3000:]
        return process.returncode, out.decode(), error.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def security():
    return ('--network', 'none', '--read-only', '--tmpfs', '/tmp:rw,nosuid,noexec,size=64m',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--cpus', '2', '--memory', '2g')


@pytest.mark.asyncio
async def test_image_is_linux_amd64_nonroot_and_contains_exact_native_source_and_drivers():
    image = os.environ['LIFE_PG_WORKER_IMAGE']
    _, metadata, _ = await docker('image', 'inspect', image)
    meta, = json.loads(metadata)
    assert meta['Id'] == image and meta['Os'] == 'linux' and meta['Architecture'] == 'amd64'
    assert meta['Config']['User'] == '10001:10001'
    assert meta['Config']['Entrypoint'] == ['python', '-m', 'hummingbot.strategy_v2.life_liquidity.storage.postgres.worker']
    files = ['hummingbot/strategy_v2/life_liquidity/storage/postgres/' + name for name in ('worker.py', 'runtime.py', 'service.py', 'migrations.py')]
    files += ['migrations/life_liquidity/' + p.name for p in sorted((ROOT / 'migrations/life_liquidity').glob('*.sql'))]
    program = '''import hashlib,json,os,platform
import asyncpg,psycopg
from hummingbot.core import clock
assert os.getuid()==10001 and platform.machine()=='x86_64' and clock.__file__.endswith('.so')
files=json.loads(__import__('sys').argv[1])
print(json.dumps({p:hashlib.sha256(open(p,'rb').read()).hexdigest() for p in files},sort_keys=True))
'''
    _, body, _ = await docker('run', '--rm', *security(), '--entrypoint', 'python', image, '-c', program, json.dumps(files))
    assert json.loads(body) == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}


@pytest.mark.asyncio
async def test_default_foreground_entrypoint_without_configuration_exits_closed_without_tty():
    code, out, error = await docker('run', '--rm', *security(), os.environ['LIFE_PG_WORKER_IMAGE'], check=False)
    assert code == 1 and json.loads(out)['event'] == 'CONFIG_INVALID'
    assert 'Traceback' not in error


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,expected', [('clean', 0), ('blocked', 2), ('failed', 1), ('startup_blocked', 2), ('late_cleanup', 2)])
async def test_real_container_signal_and_exit_deadline_with_injected_nontrading_launcher(mode, expected):
    image, name = os.environ['LIFE_PG_WORKER_IMAGE'], 'life-pg7-' + uuid4().hex[:12]
    values = dict(LIFE_ENV_FILE='', LIFE_AUTO_MIGRATE='false', LIFE_SERVICE_CONFIG='/tmp/synthetic-not-read.yml',
                  LIFE_SOURCE_FACTORY='trusted:unused', LIFE_TICK_SECONDS='.1', LIFE_STARTUP_TIMEOUT_SECONDS='1',
                  LIFE_SHUTDOWN_TIMEOUT_SECONDS='.5', LIFE_STATUS_HOST='0.0.0.0', LIFE_STATUS_PORT='8080',
                  LIFE_TELEMETRY_INTERVAL_SECONDS='.1', PYTHONPATH='/qualification')
    env = tuple(arg for key, value in values.items() for arg in ('-e', key + '=' + value))
    created = False
    try:
        await docker('run', '-d', '--name', name, '--label', 'life.pg7_test=disposable', *security(),
                     '-v', str(ROOT / 'test') + ':/qualification/test:ro', *env,
                     '--entrypoint', 'python', image, '-m', 'test.hummingbot.strategy_v2.life_liquidity.storage.worker_process', mode)
        created = True
        if mode in ('clean', 'blocked', 'late_cleanup'):
            for _ in range(150):
                _, out, _ = await docker('logs', name)
                events = [json.loads(line)['event'] for line in out.splitlines()]
                if 'STARTED' in events:
                    break
                _, status, _ = await docker('inspect', '-f', '{{.State.Status}}', name)
                assert status.strip() != 'exited', out
                await asyncio.sleep(.1)
            else:
                pytest.fail('Built worker did not reach its injected nontrading launcher')
            _, probe, _ = await docker('exec', name, 'python', '-c',
                                       "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8080/live').read().decode())")
            assert json.loads(probe)['live']
            began = monotonic()
            await docker('kill', '--signal', 'TERM', name)
        else:
            began = monotonic()
        _, result, _ = await docker('wait', name, timeout=15)
        assert int(result) == expected
        # Explicit synthetic container-functional ceiling; no financial age,
        # quote, cleanup or Cloud Run limit is widened by this outer watchdog.
        assert monotonic() - began < 10
        _, out, error = await docker('logs', name)
        assert 'synthetic-do-not-disclose-credential' not in out + error
        events = [json.loads(line)['event'] for line in out.splitlines()]
        assert ('FIXTURE_CLEANUP_DONE' in events) is (mode == 'clean')
        assert ('STOPPED' in events) is (mode == 'clean')
        if mode in ('clean', 'blocked', 'late_cleanup'):
            assert events.index('FIXTURE_REVOKED') < events.index('FIXTURE_CLEANUP_STARTED')
    finally:
        if created:
            await docker('rm', '-f', name)

"""PG2 real-process qualification in a new disposable local DB; no .env reads."""
import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations import IMAGE, docker
from uuid import uuid4


def main():
    import asyncpg  # noqa: F401 -- mandatory for this opt-in real-process runner.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', choices=('all', 'recorder', 'capital'), default='all')
    parser.add_argument('--linux-image', help='Immutable locally built image ID; recorder suite only')
    args = parser.parse_args()
    if args.linux_image and (args.suite != 'recorder' or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.linux_image)):
        parser.error('--linux-image requires an immutable sha256 image ID and --suite recorder')
    output = args.output.resolve()
    if not output.is_relative_to(Path('/tmp').resolve()):
        parser.error('Use /tmp for disposable qualification logs')
    output.mkdir(parents=True, exist_ok=False)
    name, created = 'life-pg2-' + uuid4().hex[:12], False
    # Keep the local endpoint stable across the deliberate container crash.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        selected_port = probe.getsockname()[1]
    try:
        docker('run', '-d', '--name', name, '--label', 'life.pg2_test=disposable', '--cpus', '2', '--memory', '1g',
               '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=life_pg2_test', '-p', f'127.0.0.1:{selected_port}:5432', IMAGE)
        created = True
        for _ in range(100):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'life_pg2_test'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Disposable PostgreSQL not ready')
        docker('exec', name, 'psql', '-U', 'postgres', '-d', 'life_pg2_test', '-c', '''
            CREATE ROLE life_pg2_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_pg2_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_pg2_reporter LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;''')
        (output / 'postgres_version.txt').write_text(docker('exec', name, 'psql', '-U', 'postgres', '-Atc', 'SELECT version()') + '\n')
        port = docker('port', name, '5432/tcp').split(':')[-1]
        env = {**os.environ, 'LIFE_PG_TEST_DSN': f'postgresql://postgres@127.0.0.1:{port}/life_pg2_test',
               'LIFE_PG_TEST_CONTAINER': name}
        selector = Path(__file__).with_name('postgres')
        if args.suite == 'recorder':
            selector /= 'test_postgres_recorder.py'
        elif args.suite == 'capital':
            selector /= 'test_postgres_capital.py'
        command = [sys.executable, '-m', 'pytest', '-q', str(selector),
                   '--junitxml=' + str(output / 'integration.xml')]
        with (output / 'integration.log').open('w') as log:
            if args.linux_image:
                root = Path(__file__).resolve().parents[5]
                manifest_paths = list((root / 'migrations/life_liquidity').glob('*.sql'))
                manifest_paths += [root / name for name in (
                    'hummingbot/model/sql_connection_manager.py', 'hummingbot/model/postgres_recorder.py',
                    'hummingbot/strategy_v2/life_liquidity/storage/postgres/spot.py',
                    'hummingbot/strategy_v2/life_liquidity/storage/postgres/runtime.py',
                    'controllers/generic/life_liquidity.py')]
                manifest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in manifest_paths}
                docker_prefix = ['docker', 'run', '--rm', '--network', 'container:' + name,
                                 '--cpus', '2', '--memory', '2g',
                                 '-v', str(root / 'test') + ':/qualification/test:ro',
                                 '-v', str(output) + ':/results',
                                 '-e', 'PYTHONPATH=/home/hummingbot:/qualification',
                                 '-e', 'LIFE_PG_TEST_DSN=postgresql://postgres@127.0.0.1:5432/life_pg2_test',
                                 '--entrypoint', 'python', args.linux_image]
                smoke = subprocess.run(docker_prefix + ['-m',
                                                        'test.hummingbot.strategy_v2.life_liquidity.storage.linux_image_smoke', json.dumps(manifest)],
                                       stdout=log, stderr=subprocess.STDOUT)
                if smoke.returncode:
                    print('Linux image import/package smoke failed; see integration.log')
                    return smoke.returncode
                command = docker_prefix + ['-m', 'pytest', '-q',
                                           '/qualification/test/hummingbot/strategy_v2/life_liquidity/storage/postgres/test_postgres_recorder.py',
                                           '--junitxml=/results/integration.xml', '-o', 'cache_dir=/tmp/pytest-cache']
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        print((output / 'integration.log').read_text())
        return result.returncode
    finally:
        if created:
            (output / 'postgres.log').write_text(docker('logs', name))
            docker('rm', '-f', name)


if __name__ == '__main__':
    sys.exit(main())

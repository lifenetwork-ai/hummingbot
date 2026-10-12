"""Opt-in migration tests in an isolated Docker DB; logs go only to the given /tmp output."""
import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

IMAGE = 'postgres@sha256:e17e86066e5ef83e0952a9347f5c792b7ece00972e2aa787a6986f471b3dd3d5'  # noqa: documentation


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.STDOUT).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(Path('/tmp').resolve()):
        parser.error('Use /tmp for local test logs')
    output.mkdir(parents=True, exist_ok=False)
    name, created = 'life-migrations-' + uuid.uuid4().hex[:12], False
    try:
        docker('run', '-d', '--name', name, '--label', 'life.migration_test=disposable',
               '--cpus', '2', '--memory', '1g', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
               '-e', 'POSTGRES_DB=life_migrations_test', '-p', '127.0.0.1::5432', IMAGE)
        created = True
        for _ in range(100):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1',
                                    '-U', 'postgres', '-d', 'life_migrations_test'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Disposable PostgreSQL not ready')
        docker('exec', name, 'psql', '-U', 'postgres', '-d', 'life_migrations_test', '-c', '''
            CREATE ROLE life_migrator_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_runtime_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_reporter_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;''')
        version = docker('exec', name, 'psql', '-U', 'postgres', '-d', 'life_migrations_test', '-Atc', 'SELECT version()')
        (output / 'postgres_version.txt').write_text(version + '\n')
        port = docker('port', name, '5432/tcp').split(':')[-1]
        env = {**os.environ, 'LIFE_PG_MIGRATION_TEST_DSN':
               f'postgresql://postgres@127.0.0.1:{port}/life_migrations_test'}
        command = [sys.executable, '-m', 'pytest', '-q', str(Path(__file__).with_name('pg_migration_cases.py')),
                   '--junitxml=' + str(output / 'integration.xml')]
        with (output / 'integration.log').open('w') as log:
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        print((output / 'integration.log').read_text())
        return result.returncode
    finally:
        if created:
            (output / 'postgres.log').write_text(docker('logs', name))
            docker('rm', '-f', name)


if __name__ == '__main__':
    sys.exit(main())

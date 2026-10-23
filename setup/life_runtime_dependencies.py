"""Derive a runtime prefix from exact locked build artifacts, without a solver.

Only conda-owned packages absent from the reviewed runtime lock are removed.
Retained native dependency edges and exact installed artifact hashes are checked
before mutation. Runtime versions remain identical to the compiled environment.
"""
import argparse
import json
import re
import subprocess
from pathlib import Path

ARTIFACT = re.compile(r'https://conda\.anaconda\.org/conda-forge/(linux-64|noarch)/[^/#?]+#[a-f0-9]{64}')
NAME = re.compile(r'[a-z0-9_][a-z0-9_.-]*')


def lock(path):
    lines = [v.strip() for v in Path(path).read_text().splitlines() if v.strip() and not v.startswith('#')]
    if (len(lines) < 2 or lines[0] != '@EXPLICIT' or len(set(lines)) != len(lines)
            or any(ARTIFACT.fullmatch(v) is None for v in lines[1:])):
        raise ValueError('RUNTIME_LOCK_INVALID')
    return set(lines[1:])


def records(prefix):
    return [json.loads(p.read_text()) for p in (Path(prefix) / 'conda-meta').glob('*.json')]


def removals(build, runtime, installed, wheel_names=()):
    by_name = {r['name']: r for r in installed}
    if (not runtime or not runtime <= build or len(by_name) != len(installed)
            or any(NAME.fullmatch(n) is None for n in by_name)
            or {r['url'] + '#' + r['sha256'] for r in installed} != build):
        raise ValueError('RUNTIME_ARTIFACT_MISMATCH')
    retained = {n for n, r in by_name.items() if r['url'] + '#' + r['sha256'] in runtime}
    for name in retained:
        for dependency in by_name[name]['depends']:
            dep = re.split(r'[ <>=!~]', dependency)[0]
            if not dep.startswith('__') and dep not in retained and not (dep not in by_name and dep in wheel_names):
                raise ValueError('RUNTIME_NATIVE_DEPENDENCY_MISSING:' + name + ':' + dep)
    return sorted(set(by_name) - retained)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--locks', required=True)
    parser.add_argument('--conda', required=True)
    args = parser.parse_args()
    locks = Path(args.locks)
    build, runtime = lock(locks / 'conda.lock'), lock(locks / 'runtime.conda.lock')
    wheel_names = set()
    for line in (locks / 'pip.lock').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9-]+)==[^\s]+ --hash=sha256:[a-f0-9]{64}', line)
        if match is None:
            raise ValueError('RUNTIME_PIP_LOCK_INVALID')
        wheel_names.add(match[1])
    names = removals(build, runtime, records(args.prefix), wheel_names)
    if names:
        subprocess.run([args.conda, 'remove', '-y', '--prefix', args.prefix,
                        '--force-remove', '--offline', *names], check=True)
    removals(runtime, runtime, records(args.prefix), wheel_names)
    subprocess.run([str(Path(args.prefix) / 'bin/python'), '-m', 'pip', 'check'], check=True)


if __name__ == '__main__':
    main()

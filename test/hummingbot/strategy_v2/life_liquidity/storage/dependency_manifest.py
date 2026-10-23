"""Verify the artifact's build inputs and installed Python dependency graph."""
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path


def verify_dependency_locks(root, installed_locks):
    root, installed_locks = Path(root), Path(installed_locks)
    for suffix in ('conda', 'runtime.conda', 'pip'):
        expected = root / ('setup/locks/life-worker-linux-64.' + suffix + '.lock')
        actual = installed_locks / (suffix + '.lock')
        if (expected.is_symlink() or actual.is_symlink() or not expected.is_file() or not actual.is_file()
                or expected.read_bytes() != actual.read_bytes()):
            raise ValueError('IMAGE_DEPENDENCY_LOCK_MISMATCH')

    def conda_artifacts(name):
        lines = [v for v in (installed_locks / name).read_text().splitlines() if v and not v.startswith('#')]
        if (len(lines) < 2 or lines[0] != '@EXPLICIT' or len(set(lines)) != len(lines)
                or any(re.fullmatch(r'https://conda\.anaconda\.org/conda-forge/(linux-64|noarch)/[^/#?]+#[a-f0-9]{64}', v)
                       is None for v in lines[1:])):
            raise ValueError('IMAGE_CONDA_LOCK_INVALID')
        return set(lines[1:])

    artifacts = conda_artifacts('conda.lock')
    runtime = conda_artifacts('runtime.conda.lock')
    if not runtime <= artifacts:
        raise ValueError('IMAGE_CONDA_RUNTIME_LOCK_INVALID')
    records = [json.loads(p.read_text()) for p in (Path(sys.prefix) / 'conda-meta').glob('*.json')]
    if (len(records) != len(runtime)
            or {r['url'] + '#' + r['sha256'] for r in records} != set(runtime)):
        raise ValueError('IMAGE_CONDA_RUNTIME_MISMATCH')
    pins = []
    for line in (installed_locks / 'pip.lock').read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z0-9-]+)==([^\s]+) --hash=sha256:([a-f0-9]{64})', line)
        if match is None:
            raise ValueError('IMAGE_PIP_LOCK_INVALID')
        name, version, _ = match.groups()
        if importlib.metadata.version(name) != version:
            raise ValueError('IMAGE_DEPENDENCY_VERSION_MISMATCH:' + name)
        pins.append(name)
    if not pins or len(set(pins)) != len(pins):
        raise ValueError('IMAGE_PIP_LOCK_INVALID')
    # Check every installed distribution, including conda-provided Python
    # transitive requirements, rather than only the declared direct drivers.
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True, capture_output=True, timeout=30)
    return {suffix: hashlib.sha256((installed_locks / (suffix + '.lock')).read_bytes()).hexdigest()
            for suffix in ('conda', 'runtime.conda', 'pip')}

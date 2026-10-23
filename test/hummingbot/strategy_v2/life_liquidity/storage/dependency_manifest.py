"""Verify the artifact's build inputs and installed Python dependency graph."""
import hashlib
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path


def verify_dependency_locks(root, installed_locks):
    root, installed_locks = Path(root), Path(installed_locks)
    for suffix in ('conda', 'pip'):
        expected = root / ('setup/locks/life-worker-linux-64.' + suffix + '.lock')
        actual = installed_locks / (suffix + '.lock')
        if (expected.is_symlink() or actual.is_symlink() or not expected.is_file() or not actual.is_file()
                or expected.read_bytes() != actual.read_bytes()):
            raise ValueError('IMAGE_DEPENDENCY_LOCK_MISMATCH')
    conda = (installed_locks / 'conda.lock').read_text().splitlines()
    if '@EXPLICIT' not in conda:
        raise ValueError('IMAGE_CONDA_LOCK_INVALID')
    artifacts = [v for v in conda if v and not v.startswith('#') and v != '@EXPLICIT']
    if not artifacts or any(re.fullmatch(r'https://conda\.anaconda\.org/conda-forge/(linux-64|noarch)/[^/#?]+#([a-f0-9]{64})', v) is None for v in artifacts):
        raise ValueError('IMAGE_CONDA_LOCK_INVALID')
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
            for suffix in ('conda', 'pip')}

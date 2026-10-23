"""Exact packaged execution inputs, excluding host binaries and secret files."""
import hashlib
import subprocess
from pathlib import Path


def execution_manifest(root):
    root = Path(root).resolve()
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--',
         'bin', 'hummingbot', 'controllers', 'scripts', 'migrations/life_liquidity', 'setup/locks'], cwd=root).decode().split('\0')
    result = {}
    for name in sorted(set(filter(None, names))):
        relative = Path(name)
        if any(part.startswith('.env') for part in relative.parts):
            continue
        if relative.suffix not in ('.py', '.pyx', '.pxd', '.pyi', '.json', '.yaml', '.yml', '.sql', '.lock') and name != 'bin/hbot':
            continue
        path = root / relative
        if (relative.is_absolute() or '..' in relative.parts or not path.is_file()
                or any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1))):
            raise ValueError('IMAGE_SOURCE_UNSAFE')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:
        raise ValueError('IMAGE_SOURCE_EMPTY')
    return result


def verify_execution_manifest(root, expected):
    """Runs inside the artifact; source mounted from the host is never used."""
    root = Path(root)
    if not isinstance(expected, dict) or not expected:
        raise ValueError('IMAGE_SOURCE_EMPTY')
    for name, digest in expected.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('IMAGE_SOURCE_UNSAFE')
        path = root / relative
        if (any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1))
                or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('IMAGE_SOURCE_MISMATCH:' + name)

"""An artifact must not qualify with a stale non-entrypoint execution module."""
import subprocess
from test.hummingbot.strategy_v2.life_liquidity.storage.image_manifest import (
    execution_manifest,
    verify_execution_manifest,
)

import pytest


def sources(tmp_path, monkeypatch):
    names = ['hummingbot/strategy_v2/life_liquidity/storage/postgres/hedge_source.py',
             'controllers/generic/life_liquidity.py', 'hummingbot/core/clock.pyx',
             'migrations/life_liquidity/000001_original.sql', 'bin/hbot']
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original execution input')
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: ('\0'.join(names) + '\0').encode())
    return names


@pytest.mark.parametrize('fault', ['changed', 'missing', 'symlink'])
def test_changed_nested_hedge_module_cannot_hide_behind_unchanged_entrypoints(tmp_path, monkeypatch, fault):
    names = sources(tmp_path, monkeypatch)
    manifest = execution_manifest(tmp_path)
    assert set(manifest) == set(names)
    verify_execution_manifest(tmp_path, manifest)
    path = tmp_path / names[0]
    if fault == 'changed':
        path.write_text('stale implementation')
    else:
        path.unlink()
        if fault == 'symlink':
            path.symlink_to(tmp_path / names[1])
    with pytest.raises(ValueError, match='IMAGE_SOURCE_MISMATCH'):
        verify_execution_manifest(tmp_path, manifest)


def test_manifest_never_reads_secret_files_or_host_binaries(tmp_path, monkeypatch):
    names = sources(tmp_path, monkeypatch)
    private = ['hummingbot/.env', 'hummingbot/.env.json', 'hummingbot/core/clock.so']
    for name in private:
        (tmp_path / name).symlink_to('/not-readable')
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: ('\0'.join(names + private) + '\0').encode())
    assert set(execution_manifest(tmp_path)) == set(names)


def test_missing_or_symlinked_execution_input_cannot_be_certified(tmp_path, monkeypatch):
    names = sources(tmp_path, monkeypatch)
    path = tmp_path / names[0]
    path.unlink()
    with pytest.raises(ValueError, match='IMAGE_SOURCE_UNSAFE'):
        execution_manifest(tmp_path)
    path.symlink_to(tmp_path / names[1])
    with pytest.raises(ValueError, match='IMAGE_SOURCE_UNSAFE'):
        execution_manifest(tmp_path)

import importlib.metadata
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.dependency_manifest import verify_dependency_locks

import pytest


def locks(tmp_path, monkeypatch):
    prefix = tmp_path / 'prefix'
    (prefix / 'conda-meta').mkdir(parents=True)
    (prefix / 'conda-meta/example.json').write_text(json.dumps({
        'name': 'example', 'url': 'https://conda.anaconda.org/conda-forge/linux-64/example.conda', 'sha256': 'a' * 64}))
    monkeypatch.setattr('sys.prefix', str(prefix))
    source = tmp_path / 'source'
    installed = tmp_path / 'installed'
    (source / 'setup/locks').mkdir(parents=True)
    installed.mkdir()
    content = dict(conda='@EXPLICIT\nhttps://conda.anaconda.org/conda-forge/linux-64/example.conda#' + 'a' * 64 + '\n',
                   pip='pip==' + importlib.metadata.version('pip') + ' --hash=sha256:' + 'b' * 64 + '\n')
    content['runtime.conda'] = content['conda']
    for suffix, value in content.items():
        (source / ('setup/locks/life-worker-linux-64.' + suffix + '.lock')).write_text(value)
        (installed / (suffix + '.lock')).write_text(value)
    return source, installed


@pytest.mark.parametrize('fault', ['missing', 'changed', 'symlink', 'unpinned_conda', 'unpinned_pip', 'wrong_version', 'duplicate'])
def test_artifact_dependency_gate_rejects_missing_drifted_or_unlocked_inputs(tmp_path, monkeypatch, fault):
    source, installed = locks(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: calls.append(args))
    path = installed / 'pip.lock'
    if fault == 'missing':
        path.unlink()
    elif fault == 'changed':
        path.write_text(path.read_text() + '# changed\n')
    elif fault == 'symlink':
        path.unlink()
        path.symlink_to(source / 'setup/locks/life-worker-linux-64.pip.lock')
    else:
        if fault == 'unpinned_conda':
            path = installed / 'conda.lock'
            path.write_text(path.read_text().split('#')[0] + '\n')
        elif fault == 'unpinned_pip':
            path.write_text('pip>=1\n')
        elif fault == 'wrong_version':
            path.write_text('pip==0.0.0 --hash=sha256:' + 'b' * 64 + '\n')
        elif fault == 'duplicate':
            path.write_text(path.read_text() * 2)
        (source / ('setup/locks/life-worker-linux-64.' + path.name)).write_bytes(path.read_bytes())
    with pytest.raises(ValueError, match='IMAGE_'):
        verify_dependency_locks(source, installed)
    assert not calls


@pytest.mark.parametrize('fault', ['missing', 'foreign', 'hash'])
def test_installed_conda_metadata_must_match_exact_runtime_profile(tmp_path, monkeypatch, fault):
    source, installed = locks(tmp_path, monkeypatch)
    row = tmp_path / 'prefix/conda-meta/example.json'
    if fault == 'missing':
        row.unlink()
    elif fault == 'foreign':
        row.with_name('foreign.json').write_text(row.read_text())
    else:
        data = json.loads(row.read_text())
        data['sha256'] = 'b' * 64
        row.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='IMAGE_CONDA_RUNTIME_MISMATCH'):
        verify_dependency_locks(source, installed)


def test_exact_build_runtime_and_wheel_inputs_are_verified(tmp_path, monkeypatch):
    source, installed = locks(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr('subprocess.run', lambda *args, **kwargs: calls.append(args))
    assert set(verify_dependency_locks(source, installed)) == {'conda', 'runtime.conda', 'pip'}
    assert len(calls) == 1

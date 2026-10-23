"""Runtime slimming cannot silently change versions or break native dependencies."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
spec = importlib.util.spec_from_file_location('life_runtime_dependencies', ROOT / 'setup/life_runtime_dependencies.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def row(name, deps=()):
    return dict(name=name, depends=list(deps), url='https://conda.anaconda.org/conda-forge/linux-64/' + name + '-1-0.conda',
                sha256='a' * 64)


def artifact(r):
    return r['url'] + '#' + r['sha256']


def test_exact_runtime_subset_removes_only_unreferenced_build_packages():
    python, compiler, libc = row('python', ('libc >=1', '__glibc >=2')), row('compiler'), row('libc')
    build = {artifact(r) for r in (python, compiler, libc)}
    runtime = {artifact(r) for r in (python, libc)}
    assert module.removals(build, runtime, [python, compiler, libc]) == ['compiler']


@pytest.mark.parametrize('fault', ['missing_native_dependency', 'changed_hash', 'missing_installed', 'duplicate_name', 'foreign_installed'])
def test_changed_or_incomplete_runtime_profile_fails_before_mutation(fault):
    python, libc, compiler = row('python', ('libc >=1',)), row('libc'), row('compiler')
    rows = [python, libc, compiler]
    build = {artifact(r) for r in rows}
    runtime = {artifact(r) for r in (python, libc)}
    if fault == 'missing_native_dependency':
        runtime.remove(artifact(libc))
    elif fault == 'changed_hash':
        runtime = {v.replace('#' + 'a' * 64, '#' + 'b' * 64) for v in runtime}
    elif fault == 'missing_installed':
        rows.pop()
    elif fault == 'duplicate_name':
        rows.append(dict(python, sha256='b' * 64))
    else:
        rows.append(row('foreign'))
    with pytest.raises(ValueError):
        module.removals(build, runtime, rows)


def test_lock_parser_rejects_duplicates_unhashed_and_nonexplicit(tmp_path):
    artifact_line = artifact(row('python'))
    for text in (artifact_line, '@EXPLICIT\n' + artifact_line + '\n' + artifact_line,
                 '@EXPLICIT\nhttps://example.com/not-pinned', '@EXPLICIT\n'):
        file = tmp_path / 'lock'
        file.write_text(text)
        with pytest.raises(ValueError):
            module.lock(file)


def test_packaged_runtime_preserves_versions_and_has_no_toolchain():
    build = module.lock(ROOT / 'setup/locks/life-worker-linux-64.conda.lock')
    runtime = module.lock(ROOT / 'setup/locks/life-worker-linux-64.runtime.conda.lock')
    assert runtime < build
    assert len(build - runtime) == 16
    for name in ('rust', 'gcc_impl_linux-64', 'sysroot_linux-64', 'cython', 'conda-build'):
        assert not any('/' + name + '-' in v for v in runtime)


def test_pinned_wheel_can_satisfy_an_original_mixed_dependency_but_not_mask_removal():
    python, ffi = row('python', ('cffi >=2',)), row('cffi')
    build = {artifact(python)}
    assert module.removals(build, build, [python], {'cffi'}) == []
    with pytest.raises(ValueError, match='NATIVE_DEPENDENCY_MISSING'):
        module.removals(build | {artifact(ffi)}, build, [python, ffi], {'cffi'})

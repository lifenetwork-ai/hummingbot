"""Acceptance orchestration cannot infer completion from a partial green run."""
import hashlib
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.pg_acceptance import (
    GATES,
    OPEN_REQUIREMENTS,
    command,
    gate_result,
    qualification_lock,
    source_manifest,
    verdict,
)

import pytest


def test_map_retains_native_file_product_physical_and_artifact_gates():
    names = [g.name for g in GATES]
    assert len(names) == len(set(names))
    assert set(names) == {'file_contracts', 'native', 'physical_pitr', 'foreground', 'linux_image', 'infrastructure', 'product_o8'}
    assert all(g.owner and g.requirements for g in GATES)
    assert {'PG6.1', 'PG6.2', 'PG6.3', 'PG6.4', 'PG6.5', 'PG6.6', 'PG7.1', 'PG7.8', 'PG8.3', 'PG8.4', 'PG8.6'} <= set(OPEN_REQUIREMENTS)


def test_zero_exit_with_missing_truncated_or_skipped_xml_is_not_qualification(tmp_path):
    folder = tmp_path / 'gate'
    folder.mkdir()
    gate = next(g for g in GATES if g.name == 'native')
    assert not gate_result(gate, folder, 0)['qualified']
    xml = folder / 'integration.xml'
    for body in ('<testsuites>', '<testsuites/>', '<testsuites><testsuite tests="0" errors="0" failures="0" skipped="0"/></testsuites>',
                 '<testsuites><testsuite tests="2" errors="0" failures="0" skipped="1"/></testsuites>',
                 '<testsuites><testsuite tests="1" errors="0" failures="1" skipped="0"/></testsuites>'):
        xml.write_text(body)
        assert not gate_result(gate, folder, 0)['qualified']
    xml.write_text('<testsuites><testsuite tests="2" errors="0" failures="0" skipped="0"><testcase name="a"/><testcase name="b"/></testsuite></testsuites>')
    assert gate_result(gate, folder, 0)['qualified']
    assert not gate_result(gate, folder, 2)['qualified']


def test_product_candidate_success_cannot_claim_o8_closure(tmp_path):
    gate = next(g for g in GATES if g.name == 'product_o8')
    (tmp_path / 'summary.json').write_text(json.dumps({'offline_complete': False, 'unresolved_review': ['O.8.4']}))
    assert not gate_result(gate, tmp_path, 0)['qualified']
    (tmp_path / 'summary.json').write_text(json.dumps({'offline_complete': True, 'unresolved_review': []}))
    assert not gate_result(gate, tmp_path, 0)['qualified']  # Original case reports are required.
    tests = {'original::case': dict(setup='passed', call='passed', teardown='passed')}
    raw = json.dumps(tests).encode()
    (tmp_path / 'tests.json').write_bytes(raw)
    report = dict(offline_complete=True, unresolved_review=[], test_count=1, skipped=0, failed=0,
                  test_results_sha256=hashlib.sha256(raw).hexdigest())
    (tmp_path / 'summary.json').write_text(json.dumps(report))
    assert gate_result(gate, tmp_path, 0)['qualified']
    tests['original::case']['teardown'] = 'failed'
    (tmp_path / 'tests.json').write_text(json.dumps(tests))
    assert not gate_result(gate, tmp_path, 0)['qualified']


def test_product_candidate_flag_is_explicit_and_strict_run_can_qualify_original_closure(tmp_path):
    gate = next(g for g in GATES if g.name == 'product_o8')
    assert '--candidate' not in command(gate, tmp_path, image='unused', terraform='unused')
    assert '--candidate' in command(gate, tmp_path, image='unused', terraform='unused', candidate=True)


def test_two_orchestrators_cannot_overlap_and_old_symlink_is_not_followed(tmp_path):
    path = tmp_path / 'gate.lock'
    with qualification_lock(path):
        with pytest.raises(BlockingIOError):
            with qualification_lock(path):
                pytest.fail('Concurrent qualification admitted')
    with qualification_lock(path):
        pass
    path.unlink()
    path.symlink_to(tmp_path / 'never-created')
    with pytest.raises(OSError):
        with qualification_lock(path):
            pass
    assert not (tmp_path / 'never-created').exists()


def test_source_identity_hashes_names_and_bytes_but_never_opens_dotenv(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / 'hummingbot').mkdir()
    path = tmp_path / 'hummingbot/example.py'
    path.write_text('original\n')
    (tmp_path / '.env').symlink_to('/not-readable-credential')
    monkeypatch.setattr(subprocess, 'check_output', lambda *a, **k: b'hummingbot/example.py\0.env\0')
    first = source_manifest(tmp_path)
    assert set(first) == {'hummingbot/example.py'}
    path.write_text('changed\n')
    assert source_manifest(tmp_path) != first
    extension = tmp_path / 'hummingbot/native.so'
    extension.write_bytes(b'synthetic-compiled-input')
    assert 'hummingbot/native.so' in source_manifest(tmp_path)
    path.unlink()
    path.symlink_to('/not-readable-credential')
    with pytest.raises(ValueError, match='SOURCE_UNSAFE'):
        source_manifest(tmp_path)


def test_all_green_gates_still_require_remaining_implementation_and_source_stability():
    rows = {g.name: {'qualified': True} for g in GATES}
    assert not verdict(rows, source_unchanged=True, remaining=OPEN_REQUIREMENTS)
    assert not verdict(rows, source_unchanged=False, remaining={})
    assert not verdict({k: v for k, v in rows.items() if k != 'physical_pitr'}, source_unchanged=True, remaining={})
    assert verdict(rows, source_unchanged=True, remaining={})
    rows['native'] = {'qualified': False}
    assert not verdict(rows, source_unchanged=True, remaining={})

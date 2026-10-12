"""Warm checkpoint proofs require current exact bytes, never file metadata."""
import json
import os
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity, checkpoint, restore
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hummingbot.strategy_v2.life_liquidity import checkpoint_archive as module
from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import CheckpointArchive, encode
from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze


def warm(tmp_path):
    journal = checkpoint(tmp_path)
    journal.migrate_archive(capacity())
    with journal.inspect():
        pass
    return journal


def segment_paths(journal):
    manifest = json.loads(journal.path.read_bytes())
    return [journal.archive.directory / (entry['sha256'] + '.json')
            for branch in manifest['archives'] for entry in branch['segments']]


@pytest.mark.parametrize('operation', ['inspect', 'clock', 'active_claim'])
def test_identical_segments_are_read_without_rehashing(tmp_path, monkeypatch, operation):
    journal = warm(tmp_path)
    image = journal.archive._image
    read = Mock(wraps=module.read_limited)
    digest = Mock(wraps=module.hashlib.sha256)
    monkeypatch.setattr(module, 'read_limited', read)
    monkeypatch.setattr(module.hashlib, 'sha256', digest)
    if operation == 'inspect':
        with journal.inspect() as state:
            assert state == journal._state
        assert journal.archive._image is image
    else:
        with journal.locked(shallow=True) as state:
            if operation == 'clock':
                state['clock'] = 1
            else:
                state['claims'] = {'pending': {'wire': 'active-wire'}}
            journal.commit(state)
        with journal.inspect() as state:
            assert state['clock'] == (1 if operation == 'clock' else 0)
        assert journal.archive._image[2][('settled',)][0] is image[2][('settled',)][0]
    assert digest.call_count == 0
    # Every referenced segment is actually read even on an unchanged warm cut.
    paths = segment_paths(journal)
    assert all(any(call.args[0] == path for call in read.call_args_list) for path in paths)


def test_changed_financial_segment_revalidates_only_changed_bytes(tmp_path, monkeypatch):
    journal = warm(tmp_path)
    image = journal.archive._image
    manifest = json.loads(journal.path.read_bytes())
    row = manifest['archives'][0]['segments'][-1]
    blob = encode({'4': {'wire': 'changed-financial-identity'}})
    row.update(sha256=module.hashlib.sha256(blob).hexdigest(), bytes=len(blob))
    (journal.archive.directory / (row['sha256'] + '.json')).write_bytes(blob)
    raw = encode(manifest)
    digest = Mock(wraps=module.hashlib.sha256)
    monkeypatch.setattr(module.hashlib, 'sha256', digest)
    state = journal.archive.read(raw, freeze=_freeze)
    assert state['settled']['4']['wire'] == 'changed-financial-identity'
    assert state['settled'] != image[3]['settled']
    assert digest.call_count == 1
    assert digest.call_args.args == (blob,)
    # A changed manifest alone cannot make a stale live writer current.
    journal.path.write_bytes(raw)
    with pytest.raises(ValueError, match='STALE'):
        with journal.inspect():
            pass


def test_cold_reader_hashes_all_segments_before_reusing_any_image(tmp_path, monkeypatch):
    journal = warm(tmp_path)
    archive = CheckpointArchive(journal.path, journal.archive.policy)
    digest = Mock(wraps=module.hashlib.sha256)
    monkeypatch.setattr(module.hashlib, 'sha256', digest)
    raw = journal.path.read_bytes()
    assert archive.read(raw, freeze=_freeze) == journal._state
    assert digest.call_count == len(segment_paths(journal))
    digest.reset_mock()
    archive.read(raw, freeze=_freeze)
    assert digest.call_count == 0


@pytest.mark.parametrize('fault', [
    'same_stat', 'missing', 'symlink', 'directory_symlink',
    'digest', 'count', 'length', 'policy', 'capacity',
])
def test_exact_byte_reuse_denies_corrupt_or_rebound_segments(tmp_path, fault):
    journal = warm(tmp_path)
    image = journal.archive._image
    manifest = json.loads(journal.path.read_bytes())
    row = manifest['archives'][0]['segments'][0]
    path = segment_paths(journal)[0]
    blob, stat = path.read_bytes(), path.stat()
    if fault == 'same_stat':
        path.write_bytes(blob.replace(b'wire-0', b'wire-x'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif fault == 'missing':
        path.unlink()
    elif fault == 'symlink':
        target = tmp_path / 'foreign-segment'
        target.write_bytes(blob)
        path.unlink()
        path.symlink_to(target)
    elif fault == 'directory_symlink':
        directory = journal.archive.directory
        target = tmp_path / 'foreign-directory'
        directory.rename(target)
        directory.symlink_to(target)
    else:
        if fault == 'digest':
            # Identical bytes at a new, false digest must not inherit old proof.
            row['sha256'] = '0' * 64
            (journal.archive.directory / (row['sha256'] + '.json')).write_bytes(blob)
        elif fault == 'count':
            manifest['archives'][0]['segments'][-1]['count'] += 1
            manifest['archives'][0]['count'] += 1
        elif fault == 'length':
            row['bytes'] -= 1
        elif fault == 'policy':
            manifest['archive_policy']['max_segments'] += 1
        else:
            manifest['archives'][0]['segments'] *= 10
        journal.path.write_bytes(encode(manifest))
    with pytest.raises((ValueError, OSError)):
        with journal.inspect():
            pass
    assert journal.archive._image is image  # A failed read publishes no proof.
    with pytest.raises((ValueError, OSError)):
        restore(journal, capacity())


@pytest.mark.parametrize('fault', ['memory', 'uncertain', 'root_during_read'])
def test_policy_fences_remain_binding_with_warm_byte_proof(tmp_path, monkeypatch, fault):
    journal = warm(tmp_path)
    if fault == 'memory':
        journal._state = {**journal._state, 'clock': 9}
    elif fault == 'uncertain':
        journal._uncertain = True
    else:
        original = module.read_limited
        changed = False

        def read(path, maximum):
            nonlocal changed
            value = original(path, maximum)
            if path.parent == journal.archive.directory and not changed:
                changed = True
                manifest = json.loads(journal.path.read_bytes())
                manifest['state']['clock'] = 1
                journal.path.write_bytes(encode(manifest))
            return value
        monkeypatch.setattr(module, 'read_limited', read)
    with pytest.raises(ValueError, match='STALE'):
        with journal.inspect():
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize('history', [0, 1000, 10000])
async def test_protected_sender_does_not_rehash_verified_checkpoint_segments(tmp_path, monkeypatch, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        archive_settled_history(f)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code

        def unexpected(*args):
            raise AssertionError('unchanged policy checkpoint segment was hashed again')
        # Restrict the guard to eager policy checkpoints. Lazy WAL/reservation
        # views retain no whole-history byte image and must still hash on use.
        monkeypatch.setattr(module, 'hashlib', SimpleNamespace(sha256=unexpected))
        actions = f.c._quote_action_planner.propose()
        assert actions, f.c._quote_action_planner.reason_code
        claim, wire = send(f, actions[0])
        assert f.c.allow_create_executor_actions()
        f.v[3].sent[-1]['pre_send_check'](wire)
        assert claim.intent_id in f.v[1].claim_ids()


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', ['capital', 'joint'])
async def test_corrupted_warm_checkpoint_revokes_final_send_without_releasing_claim(tmp_path, owner):
    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_settled_history(f)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        claim, wire = send(f, f.c._quote_action_planner.propose()[0])
        journal = f.v[1].journal if owner == 'capital' else f.joint.journal
        path = segment_paths(journal)[0]
        blob, stat = path.read_bytes(), path.stat()
        assert b'past-' in blob
        path.write_bytes(blob.replace(b'past-', b'evil-', 1))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with pytest.raises(PermissionError):
            f.v[3].sent[-1]['pre_send_check'](wire)
        assert claim.intent_id in f.v[1].journal._state['claims']
        assert f.c._order_safety_reservations.has_open_intent(claim.intent_id)

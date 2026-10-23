"""PG6 detached segmented cuts retain every referenced byte and terminal ID."""
import hashlib
import os
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity, checkpoint

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.archive_source import ArchiveSource, capture_archive
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


def source(tmp_path):
    journal = checkpoint(tmp_path)
    journal.migrate_archive(capacity())
    return journal


def test_complete_detached_capture_preserves_exact_manifest_and_terminal_segments(tmp_path):
    journal = source(tmp_path)
    cut = capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5)
    assert type(cut) is ArchiveSource
    assert cut.root == journal.path.read_bytes() and cut.record_count == 5
    assert len(cut.segments) == 3
    assert dict(cut.segments) == {p.name: p.read_bytes() for p in journal.archive.directory.iterdir()}
    assert capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5) == cut
    assert len(cut.content_digest) == 64
    # Unreferenced orphan segments are not part of a logical cut.
    orphan = b'{"orphan":true}'
    (journal.archive.directory / (hashlib.sha256(orphan).hexdigest() + '.json')).write_bytes(orphan)
    assert capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5) == cut


@pytest.mark.parametrize('fault', ['missing', 'symlink', 'same_stat', 'root_symlink', 'directory_symlink', 'wrong_binding'])
def test_capture_rechecks_bytes_without_trusting_warm_verification(tmp_path, fault):
    journal = source(tmp_path)
    capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5)
    segment = next(journal.archive.directory.iterdir())
    if fault == 'missing':
        segment.unlink()
    elif fault == 'same_stat':
        stat = segment.stat()
        segment.write_bytes(segment.read_bytes().replace(b'wire-', b'fake-'))
        os.utime(segment, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif fault in ('symlink', 'root_symlink'):
        path = segment if fault == 'symlink' else journal.path
        copied = tmp_path / 'copied'
        copied.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copied)
    elif fault == 'directory_symlink':
        copied = tmp_path / 'segments'
        journal.archive.directory.rename(copied)
        journal.archive.directory.symlink_to(copied, target_is_directory=True)
    else:
        journal.archive.directory = tmp_path / 'foreign.segments'
    with pytest.raises(StorageError):
        capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5)


@pytest.mark.parametrize('limits', [{'max_bytes': 100, 'max_rows': 5}, {'max_bytes': 65536, 'max_rows': 4}])
def test_capacity_refuses_partial_cut(tmp_path, limits):
    journal = source(tmp_path)
    with pytest.raises(StorageError, match='CAPACITY'):
        capture_archive(journal.path, journal.archive, **limits)


def test_detached_source_rejects_omitted_extra_corrupt_or_relabelled_segments(tmp_path):
    journal = source(tmp_path)
    cut = capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5)
    for segments in (cut.segments[:-1], cut.segments + (('f' * 64 + '.json', b'{}'),),
                     ((cut.segments[0][0], b'{}'),) + cut.segments[1:],
                     (('f' * 64 + '.json', cut.segments[0][1]),) + cut.segments[1:]):
        with pytest.raises(StorageError):
            replace(cut, segments=segments)
    with pytest.raises(StorageError):
        replace(cut, root=cut.root.replace(b'"count":5', b'"count":6'))


def test_change_during_capture_cannot_publish_a_mixed_cut(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage import archive_source
    journal = source(tmp_path)
    original = archive_source.read_limited
    reads = 0

    def read(path, maximum):
        nonlocal reads
        raw = original(path, maximum)
        if path == journal.path:
            reads += 1
            if reads == 1:
                journal.path.write_bytes(raw.replace(b'"clock":0', b'"clock":1'))
        return raw
    monkeypatch.setattr(archive_source, 'read_limited', read)
    with pytest.raises(StorageError, match='CHANGED'):
        capture_archive(journal.path, journal.archive, max_bytes=65536, max_rows=5)

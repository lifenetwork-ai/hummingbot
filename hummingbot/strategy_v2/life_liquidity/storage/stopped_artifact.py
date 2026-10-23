"""Cold transport for the complete stopped File graph, never cutover authority.

The external SHA-256 binds every byte, including reconciliation facts omitted
from the historical core cut fingerprint. No source paths are extracted or
opened during decoding. These synchronous, bounded maintenance APIs must run
outside the trading event loop. Admission, old-key exclusion, import parity and
activation remain separate requirements.
"""
import base64
import hashlib
import os
import stat
import tempfile
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy, decode
from hummingbot.strategy_v2.life_liquidity.storage.archive_source import ArchiveSource
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, digest, encode
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import pack_source, sync_dir, unpack_source
from hummingbot.strategy_v2.life_liquidity.storage.recorder_source import capture_limits
from hummingbot.strategy_v2.life_liquidity.storage.service_source import StoppedFileServiceSource


def _limit(max_bytes, max_rows):
    capture_limits(max_bytes, max_rows)
    # Nested base64 and framing overhead; the original logical-byte bound is
    # separately checked after parsing. Never allocate from untrusted metadata.
    return 4 * max_bytes + 65536


def _capacity(source, max_bytes, max_rows):
    capture_limits(max_bytes, max_rows)
    if type(source) is not StoppedFileServiceSource:
        raise StorageError('STOPPED_ARTIFACT_SOURCE_INVALID')
    s = source.base
    size = (len(s.core.checkpoint_bytes) + len(s.service_json.encode()) + len(s.recorder.rows_json.encode())
            + sum(len(raw) for _, raw in s.core.journal_images) + sum(len(raw) for _, raw in s.ancillary_images)
            + sum(len(raw) for _, archive in source.archives for _, raw in archive.segments))
    # Same archived-record + SQL-row bound as configured capture. Ordinary
    # checkpoint collections remain byte-bounded; this is not an import batch
    # capacity certificate or a claim that their individual records were counted.
    rows = sum(a.record_count for _, a in source.archives) + sum(len(v) for v in decode(s.recorder.rows_json)['tables'].values())
    if size > max_bytes or rows > max_rows:
        raise StorageError('STOPPED_ARTIFACT_CAPACITY_EXCEEDED')


def pack_stopped_source(source, *, max_bytes, max_rows):
    _capacity(source, max_bytes, max_rows)

    def b64(value):
        return base64.b64encode(value).decode('ascii')
    raw = encode(dict(format='LIFE_STOPPED_FILE_ARTIFACT_V1', source_digest=source.content_digest,
                      base=b64(pack_source(source.base)), archives=[dict(name=name, policy=a.policy.json(), root=b64(a.root),
                                                                         segments=[[name, b64(value)] for name, value in a.segments])
                                                                    for name, a in source.archives]))
    if len(raw) > _limit(max_bytes, max_rows):
        raise StorageError('STOPPED_ARTIFACT_CAPACITY_EXCEEDED')
    return raw


def unpack_stopped_source(raw, *, expected_sha256, max_bytes, max_rows):
    maximum = _limit(max_bytes, max_rows)
    digest(expected_sha256)
    if type(raw) is not bytes:
        raise StorageError('STOPPED_ARTIFACT_INVALID')
    if len(raw) > maximum:
        raise StorageError('STOPPED_ARTIFACT_CAPACITY_EXCEEDED')
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise StorageError('STOPPED_ARTIFACT_DIGEST_MISMATCH')
    try:
        value = decode(raw)
        if (type(value) is not dict or set(value) != {'format', 'source_digest', 'base', 'archives'}
                or value['format'] != 'LIFE_STOPPED_FILE_ARTIFACT_V1' or type(value['archives']) is not list
                or encode(value) != raw):
            raise ValueError()

        def binary(value):
            if type(value) is not str:
                raise ValueError()
            return base64.b64decode(value, validate=True)
        base, archives = unpack_source(binary(value['base'])), []
        for entry in value['archives']:
            if type(entry) is not dict or set(entry) != {'name', 'policy', 'root', 'segments'} or type(entry['segments']) is not list:
                raise ValueError()
            policy = dict(entry['policy'])
            policy['paths'] = tuple(tuple(path) for path in policy['paths'])
            archive = ArchiveSource(ArchivePolicy(**policy), binary(entry['root']),
                                    tuple((name, binary(data)) for name, data in entry['segments']))
            archives.append((entry['name'], archive))
        source = StoppedFileServiceSource(base, tuple(archives))
        if source.content_digest != value['source_digest']:
            raise ValueError()
        _capacity(source, max_bytes, max_rows)
        return source
    except StorageError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise StorageError('STOPPED_ARTIFACT_INVALID') from None


def _path(path):
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve() != path.parent or not path.parent.is_dir() or path.is_symlink():
        raise StorageError('STOPPED_ARTIFACT_PATH_INVALID')
    return path


def _read(path, maximum):
    descriptor = os.open(_path(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(descriptor, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise StorageError('STOPPED_ARTIFACT_PATH_INVALID')
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise StorageError('STOPPED_ARTIFACT_CAPACITY_EXCEEDED')
    return raw


def load_stopped_artifact(path, *, expected_sha256, max_bytes, max_rows):
    try:
        raw = _read(path, _limit(max_bytes, max_rows))
        return unpack_stopped_source(raw, expected_sha256=expected_sha256, max_bytes=max_bytes, max_rows=max_rows)
    except OSError:
        raise StorageError('STOPPED_ARTIFACT_UNAVAILABLE') from None


def publish_stopped_artifact(path, source, *, max_bytes, max_rows):
    path = _path(path)
    raw = pack_stopped_source(source, max_bytes=max_bytes, max_rows=max_rows)
    sha = hashlib.sha256(raw).hexdigest()
    descriptor, temporary = tempfile.mkstemp(prefix='.stopped-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Link publishes the complete fsynced inode exclusively. A crash
            # can leave an orphan temporary file, never a partially written
            # final artifact. Different existing bytes are never overwritten.
            os.link(temporary, path)
        except FileExistsError:
            if _read(path, _limit(max_bytes, max_rows)) != raw:
                raise StorageError('STOPPED_ARTIFACT_CONFLICT') from None
        sync_dir(path.parent)
        if load_stopped_artifact(path, expected_sha256=sha, max_bytes=max_bytes, max_rows=max_rows) != source:
            raise StorageError('STOPPED_ARTIFACT_READBACK_INVALID')
        return sha
    finally:
        os.unlink(temporary)

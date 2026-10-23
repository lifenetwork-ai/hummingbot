"""Complete immutable segmented cut for offline import; never writer authority.

Capture only explicitly bound, referenced content. Orphans are not history.
No timestamp cache, directory discovery, source rewrite or partial success.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import (
    ArchivePolicy,
    CheckpointArchive,
    decode,
    read_limited,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint


def _descriptors(policy, root):
    if type(policy) is not ArchivePolicy or type(root) is not bytes or len(root) > policy.max_active_bytes:
        raise ValueError('policy')
    manifest = decode(root)
    if (type(manifest) is not dict or set(manifest) != {'schema_version', 'policy', 'archive_policy', 'state', 'archives'}
            or manifest['schema_version'] != 2 or manifest['archive_policy'] != policy.json()
            or type(manifest['state']) is not dict or type(manifest['archives']) is not list
            or len(manifest['archives']) != len(policy.paths)):
        raise ValueError('manifest')
    files, count = {}, 0
    for path, entry in zip(policy.paths, manifest['archives']):
        if (type(entry) is not dict or set(entry) != {'path', 'kind', 'count', 'segments'}
                or entry['path'] != list(path) or entry['kind'] not in ('map', 'list')
                or type(entry['count']) is not int or entry['count'] < 0
                or type(entry['segments']) is not list or len(entry['segments']) > policy.max_segments
                or CheckpointArchive._branch(manifest['state'], path) is not None):
            raise ValueError('branch')
        branch_count = 0
        for index, segment in enumerate(entry['segments']):
            if (type(segment) is not dict or set(segment) != {'sha256', 'count', 'bytes'}
                    or type(segment['sha256']) is not str or len(segment['sha256']) != 64
                    or any(v not in '0123456789abcdef' for v in segment['sha256'])
                    or type(segment['count']) is not int or not 0 < segment['count'] <= policy.records_per_segment
                    or index < len(entry['segments']) - 1 and segment['count'] != policy.records_per_segment
                    or type(segment['bytes']) is not int or not 0 < segment['bytes'] <= policy.max_segment_bytes):
                raise ValueError('segment')
            name = segment['sha256'] + '.json'
            if name in files and files[name] != segment['bytes']:
                raise ValueError('conflicting segment')
            files[name] = segment['bytes']
            branch_count += segment['count']
        if branch_count != entry['count']:
            raise ValueError('coverage')
        count += branch_count
    if len(files) > policy.max_segments or sum(files.values()) > policy.max_archive_bytes:
        raise ValueError('capacity')
    return manifest, files, count


@dataclass(frozen=True)
class ArchiveSource:
    policy: ArchivePolicy
    root: bytes
    segments: tuple[tuple[str, bytes], ...]

    def __post_init__(self):
        try:
            manifest, files, _ = _descriptors(self.policy, self.root)
            if (type(self.segments) is not tuple
                    or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not bytes
                           for row in self.segments)
                    or tuple(name for name, _ in self.segments) != tuple(sorted(files))):
                raise ValueError('files')
            chunks = {}
            for name, raw in self.segments:
                if len(raw) != files[name] or hashlib.sha256(raw).hexdigest() + '.json' != name:
                    raise ValueError('bytes')
                chunks[name] = decode(raw)
            for entry in manifest['archives']:
                keys = set()
                for descriptor in entry['segments']:
                    chunk = chunks[descriptor['sha256'] + '.json']
                    if type(chunk) is not (dict if entry['kind'] == 'map' else list) or len(chunk) != descriptor['count']:
                        raise ValueError('rows')
                    if type(chunk) is dict:
                        if keys & chunk.keys():
                            raise ValueError('duplicate identity')
                        keys.update(chunk)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise StorageError('FILE_ARCHIVE_SOURCE_INVALID') from None

    @property
    def record_count(self):
        return sum(entry['count'] for entry in decode(self.root)['archives'])

    @property
    def content_digest(self):
        return fingerprint(dict(format='LIFE_FILE_ARCHIVE_V1', policy=self.policy.json(),
                                root=hashlib.sha256(self.root).hexdigest(),
                                segments={name: hashlib.sha256(raw).hexdigest() for name, raw in self.segments}))


def capture_archive(path, archive, *, max_bytes, max_rows):
    if (type(archive) is not CheckpointArchive or any(type(v) is not int or v < 1 for v in (max_bytes, max_rows))):
        raise StorageError('FILE_ARCHIVE_CAPTURE_CONFIG_INVALID')
    path = Path(path)
    try:
        directory = path.with_name(path.name + '.segments')
        if path.is_symlink() or archive.directory != directory or directory.is_symlink() or not directory.is_dir():
            raise StorageError('FILE_ARCHIVE_SOURCE_UNSAFE')
        root = read_limited(path, min(max_bytes, archive.policy.max_active_bytes))
        _, files, count = _descriptors(archive.policy, root)
        if count > max_rows or len(root) + sum(files.values()) > max_bytes:
            raise StorageError('FILE_ARCHIVE_CAPTURE_CAPACITY_EXCEEDED')
        source = ArchiveSource(archive.policy, root, tuple(
            (name, read_limited(directory / name, size)) for name, size in sorted(files.items())))
        # A full second read detects replacement across graph collection. This
        # still needs the caller's account/writer fence before a later import.
        if read_limited(path, len(root)) != root or directory.is_symlink():
            raise StorageError('FILE_ARCHIVE_SOURCE_CHANGED')
        if any(read_limited(directory / name, len(raw)) != raw for name, raw in source.segments):
            raise StorageError('FILE_ARCHIVE_SOURCE_CHANGED')
        return source
    except StorageError:
        raise
    except ValueError as error:
        code = 'FILE_ARCHIVE_CAPTURE_CAPACITY_EXCEEDED' if 'CAPACITY' in str(error) else 'FILE_ARCHIVE_SOURCE_UNVERIFIED'
        raise StorageError(code) from None
    except (OSError, TypeError, KeyError, AttributeError):
        raise StorageError('FILE_ARCHIVE_SOURCE_UNVERIFIED') from None

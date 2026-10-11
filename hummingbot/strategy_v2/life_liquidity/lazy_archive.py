"""Read-only archive maps with segment-sized decoding and no full-history RAM index.

Cold open validates every segment and derives sorted key ranges and optional
Bloom filters. Filters only select files to read; exact matching rows remain the
authority. Permission verification still hashes all referenced bytes.
"""
import hashlib
import sqlite3
import tempfile
from bisect import bisect_left
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from heapq import merge
from pathlib import Path
from types import MappingProxyType

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import decode, read_limited
from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze

# Fixed per-segment RAM; 2 KiB avoids saturation at the frozen 500-row scale.
BLOOM_BITS = 16384


@contextmanager
def scratch_index():
    """Private, disposable cold-validation workspace; never a live authority."""
    with tempfile.TemporaryDirectory(prefix='life-verify-') as directory:
        connection = sqlite3.connect(str(Path(directory) / 'index.sqlite'))
        try:
            connection.execute('PRAGMA cache_size=-256')
            connection.execute('PRAGMA temp_store=FILE')
            connection.execute('PRAGMA journal_mode=OFF')
            yield connection
        finally:
            connection.close()


def _bits(value):
    digest = hashlib.sha256(value.encode()).digest()
    bits = 0
    for i in (0, 2, 4, 6):
        bits |= 1 << (int.from_bytes(digest[i:i + 2], 'big') % BLOOM_BITS)
    return bits


@dataclass(frozen=True)
class Segment:
    sha256: str
    count: int
    size: int
    first: str
    last: str
    bloom: int = 0


class LazyArchiveMap(Mapping):
    def __setattr__(self, name, value):
        raise TypeError('archive view is read-only')

    def __init__(self, directory, entries, *, index_field=None):
        object.__setattr__(self, 'directory', directory)
        object.__setattr__(self, 'entries', tuple(entries))
        object.__setattr__(self, 'index_field', index_field)
        object.__setattr__(self, '_ends', tuple(entry.last for entry in entries))
        object.__setattr__(self, '_count', sum(entry.count for entry in entries))
        object.__setattr__(self, '_cache', None)

    @property
    def cached_records(self):
        return 0 if self._cache is None else len(self._cache[1])

    def _image_for(self, entry):
        # An immutable decoded chunk represents a previously authenticated
        # snapshot, just like an active in-memory row. Lookup grants no order
        # permission: verify_maps always rehashes all current bytes, including
        # warm cached segments, before permission/commit cache reuse.
        cached = self._cache
        if cached is not None and cached[0] == entry.sha256:
            return cached
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError('ARCHIVE_DIRECTORY_INVALID')
        target = self.directory / (entry.sha256 + '.json')
        if target.is_symlink() or not target.is_file():
            raise ValueError('ARCHIVE_SEGMENT_UNAVAILABLE')
        raw = read_limited(target, entry.size)
        if len(raw) != entry.size or hashlib.sha256(raw).hexdigest() != entry.sha256:
            raise ValueError('ARCHIVE_SEGMENT_CHANGED')
        chunk = decode(raw)
        keys = sorted(chunk) if isinstance(chunk, dict) else []
        if (len(keys) != entry.count or not keys or keys[0] != entry.first or keys[-1] != entry.last):
            raise ValueError('ARCHIVE_COVERAGE_INVALID')
        chunk = _freeze(chunk)
        wires = None
        if self.index_field is not None:
            wires = {row[self.index_field]: key for key, row in chunk.items()}
            if len(wires) != len(chunk):
                raise ValueError('WAL_WIRE_ID_DUPLICATE')
            wires = MappingProxyType(wires)
        image = (entry.sha256, chunk, wires)
        object.__setattr__(self, '_cache', image)
        return image

    def _chunk(self, entry):
        return self._image_for(entry)[1]

    def __len__(self):
        return self._count

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise KeyError(key)
        index = bisect_left(self._ends, key)
        if index == len(self.entries) or key < self.entries[index].first:
            raise KeyError(key)
        return self._chunk(self.entries[index])[key]

    def __iter__(self):
        for key, _ in self.items():
            yield key

    def items(self):
        for entry in self.entries:
            chunk = self._chunk(entry)
            yield from ((key, chunk[key]) for key in sorted(chunk))

    def values(self):
        for _, value in self.items():
            yield value

    def find(self, value):
        if self.index_field is None:
            raise ValueError('ARCHIVE_INDEX_UNAVAILABLE')
        bits = _bits(value)
        for entry in self.entries:
            if entry.bloom & bits == bits:
                _, chunk, wires = self._image_for(entry)
                if value in wires:
                    key = wires[value]
                    return key, chunk[key]
        raise KeyError(value)

    def __eq__(self, other):
        if isinstance(other, LazyArchiveMap):
            return (self.directory == other.directory and self.entries == other.entries
                    and self.index_field == other.index_field)
        return isinstance(other, Mapping) and len(self) == len(other) and all(
            key in other and row == other[key] for key, row in self.items())


class TransformedMap(Mapping):
    def __setattr__(self, name, value):
        raise TypeError('archive view is read-only')

    def __init__(self, source, transform):
        object.__setattr__(self, 'source', source)
        object.__setattr__(self, 'transform', transform)

    @property
    def cached_records(self):
        return self.source.cached_records

    def __len__(self):
        return len(self.source)

    def __iter__(self):
        return iter(self.source)

    def __getitem__(self, key):
        return self.transform(key, self.source[key])

    def items(self):
        for key, value in self.source.items():
            yield key, self.transform(key, value)

    def values(self):
        for _, value in self.items():
            yield value

    def __eq__(self, other):
        if isinstance(other, TransformedMap) and self.transform == other.transform:
            return self.source == other.source
        return isinstance(other, Mapping) and len(self) == len(other) and all(
            key in other and row == other[key] for key, row in self.items())


class PartitionMap(Mapping):
    def __setattr__(self, name, value):
        raise TypeError('journal view is read-only')

    def __init__(self, active, settled):
        object.__setattr__(self, 'active', MappingProxyType(dict(active)))
        object.__setattr__(self, 'settled', settled)
        if any(key in settled for key in active):
            raise ValueError('ARCHIVE_PARTITION_INVALID')

    def __len__(self):
        return len(self.active) + len(self.settled)

    def __getitem__(self, key):
        return self.active[key] if key in self.active else self.settled[key]

    def __iter__(self):
        yield from self.settled
        yield from self.active

    def items(self):
        yield from merge(self.settled.items(), sorted(self.active.items()), key=lambda row: row[0])

    def values(self):
        for _, value in self.items():
            yield value

    def __eq__(self, other):
        if isinstance(other, PartitionMap):
            return self.active == other.active and self.settled == other.settled
        return isinstance(other, Mapping) and len(self) == len(other) and all(
            key in other and row == other[key] for key, row in self.items())


def open_maps(archive, raw, *, indexes=None):
    """Validate a sorted map cut one segment at a time, retaining no full image."""
    manifest = decode(raw)
    if (not isinstance(manifest, dict) or len(raw) > archive.policy.max_active_bytes
            or set(manifest) != {'schema_version', 'policy', 'archive_policy', 'state', 'archives'}
            or manifest['schema_version'] != 2 or manifest['archive_policy'] != archive.policy.json()
            or not isinstance(manifest['state'], dict) or not isinstance(manifest['archives'], list)
            or len(manifest['archives']) != len(archive.policy.paths)):
        raise ValueError('ARCHIVE_POLICY_MISMATCH')
    if archive.directory.is_symlink() or not archive.directory.is_dir():
        raise ValueError('ARCHIVE_DIRECTORY_INVALID')
    state, seen, total = manifest['state'], {}, 0
    for entry, path in zip(manifest['archives'], archive.policy.paths):
        if (not isinstance(entry, dict) or set(entry) != {'path', 'kind', 'count', 'segments'}
                or entry['path'] != list(path) or entry['kind'] != 'map'
                or type(entry['count']) is not int or entry['count'] < 0
                or not isinstance(entry['segments'], list) or len(entry['segments']) > archive.policy.max_segments
                or archive._branch(state, path) is not None):
            raise ValueError('ARCHIVE_COVERAGE_INVALID')
        segments, previous = [], None
        index_field = (indexes or {}).get(path)
        for i, segment in enumerate(entry['segments']):
            if (not isinstance(segment, dict) or set(segment) != {'sha256', 'count', 'bytes'}
                    or type(segment['sha256']) is not str or len(segment['sha256']) != 64
                    or any(c not in '0123456789abcdef' for c in segment['sha256'])
                    or type(segment['count']) is not int or not 0 < segment['count'] <= archive.policy.records_per_segment
                    or i < len(entry['segments']) - 1 and segment['count'] != archive.policy.records_per_segment
                    or type(segment['bytes']) is not int or not 0 < segment['bytes'] <= archive.policy.max_segment_bytes):
                raise ValueError('ARCHIVE_SEGMENT_INVALID')
            sha = segment['sha256']
            if sha not in seen:
                total += segment['bytes']
                seen[sha] = segment['bytes']
                if len(seen) > archive.policy.max_segments or total > archive.policy.max_archive_bytes:
                    raise ValueError('ARCHIVE_CAPACITY_EXCEEDED')
            if seen[sha] != segment['bytes']:
                raise ValueError('ARCHIVE_SEGMENT_INVALID')
            target = archive.directory / (sha + '.json')
            if target.is_symlink() or not target.is_file():
                raise ValueError('ARCHIVE_SEGMENT_UNAVAILABLE')
            blob = read_limited(target, segment['bytes'])
            if len(blob) != segment['bytes'] or hashlib.sha256(blob).hexdigest() != sha:
                raise ValueError('ARCHIVE_SEGMENT_CHANGED')
            chunk = decode(blob)
            keys = sorted(chunk) if isinstance(chunk, dict) else []
            if (not keys or len(keys) != segment['count'] or previous is not None and keys[0] <= previous):
                raise ValueError('ARCHIVE_COVERAGE_INVALID')
            bloom = 0
            if index_field is not None:
                for row in chunk.values():
                    if not isinstance(row, dict) or not isinstance(row.get(index_field), str) or not row[index_field]:
                        raise ValueError('ARCHIVE_INDEX_INVALID')
                    bloom |= _bits(row[index_field])
            segments.append(Segment(sha, len(keys), len(blob), keys[0], keys[-1], bloom))
            previous = keys[-1]
        view = LazyArchiveMap(archive.directory, segments, index_field=index_field)
        if len(view) != entry['count']:
            raise ValueError('ARCHIVE_COVERAGE_INVALID')
        state = archive._replace(state, path, view)
    return _freeze(manifest), _freeze(state)


def verify_maps(archive, state):
    """Exact byte fences without retaining or decoding all segment blobs."""
    if archive.directory.is_symlink() or not archive.directory.is_dir():
        raise ValueError('ARCHIVE_DIRECTORY_INVALID')
    for path in archive.policy.paths:
        view = archive._branch(state, path)
        for entry in view.entries:
            target = archive.directory / (entry.sha256 + '.json')
            if target.is_symlink() or not target.is_file():
                raise ValueError('ARCHIVE_SEGMENT_UNAVAILABLE')
            blob = read_limited(target, entry.size)
            if len(blob) != entry.size or hashlib.sha256(blob).hexdigest() != entry.sha256:
                raise ValueError('ARCHIVE_SEGMENT_CHANGED')

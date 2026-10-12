"""Explicit-capacity immutable segments for host-local policy checkpoints.

Segments are never garbage-collected here. Capacity exhaustion refuses a new
checkpoint, preserving financial and deduplication evidence. Schema migration
and all capacity values require an explicit caller decision.
"""
import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ARCHIVE_DUPLICATE_KEY")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def read_limited(path, maximum):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
    return raw


@dataclass(frozen=True)
class ArchivePolicy:
    paths: tuple[tuple[str, ...], ...]
    records_per_segment: int
    max_segment_bytes: int
    max_archive_bytes: int
    max_segments: int
    max_active_bytes: int

    def __post_init__(self):
        if (not isinstance(self.paths, tuple) or not self.paths
                or any(not isinstance(p, tuple) or not p or any(type(k) is not str or not k for k in p) for p in self.paths)
                or len(set(self.paths)) != len(self.paths)
                or any(a != b and b[:len(a)] == a for a in self.paths for b in self.paths)
                or any(type(v) is not int or v < 1 for v in (
                    self.records_per_segment, self.max_segment_bytes, self.max_archive_bytes,
                    self.max_segments, self.max_active_bytes))):
            raise ValueError("ARCHIVE_POLICY_INVALID")

    def json(self):
        return decode(encode(asdict(self)))


class CheckpointArchive:
    def __init__(self, path: Path, policy: ArchivePolicy):
        if not isinstance(policy, ArchivePolicy):
            raise ValueError("ARCHIVE_POLICY_INVALID")
        self.directory = path.with_name(path.name + ".segments")
        self.policy = policy
        # At most one verified logical image. Raw bytes and independently
        # decoded values are resource-bounded by the supplied policy.
        self._image = None

    def _usage(self):
        if self.directory.is_symlink():
            raise ValueError("ARCHIVE_DIRECTORY_INVALID")
        if not self.directory.exists():
            return {}
        usage = {}
        total = 0
        for file in self.directory.iterdir():
            if file.is_symlink() or not file.is_file():
                raise ValueError("ARCHIVE_DIRECTORY_INVALID")
            usage[file.name] = file.stat().st_size
            if usage[file.name] > self.policy.max_segment_bytes:
                raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
            total += usage[file.name]
            if len(usage) > self.policy.max_segments or total > self.policy.max_archive_bytes:
                raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
        return usage

    def assert_capacity(self):
        """New-risk gate; old verified cuts remain readable for cancellation."""
        self._usage()

    def read_manifest(self, path):
        return read_limited(path, self.policy.max_active_bytes)

    @staticmethod
    def _branch(state, path):
        try:
            for key in path:
                state = state[key]
        except (KeyError, TypeError) as exc:
            raise ValueError("ARCHIVE_BRANCH_INVALID") from exc
        return state

    @staticmethod
    def _replace(state, path, value):
        result = dict(state)
        if len(path) == 1:
            result[path[0]] = value
        else:
            result[path[0]] = CheckpointArchive._replace(state[path[0]], path[1:], value)
        return result

    def prepare(self, state, policy):
        """Plan the entire cut and enforce capacities before publishing files."""
        active, entries, pending = state, [], {}
        previous = getattr(self, "_lazy_branches", {}) if self._image is None else self._image[2]
        for path in self.policy.paths:
            value = self._branch(state, path)
            if not isinstance(value, (dict, list)):
                raise ValueError("ARCHIVE_BRANCH_INVALID")
            old = previous.get(path)
            if old is not None and (value is old[0] or value == old[0]):
                entry = old[1]
            else:
                kind = "map" if isinstance(value, dict) else "list"
                values = sorted(value.items()) if kind == "map" else value
                segments = []
                size = self.policy.records_per_segment
                for offset in range(0, len(values), size):
                    chunk = values[offset:offset + size]
                    raw = encode(dict(chunk) if kind == "map" else chunk)
                    if len(raw) > self.policy.max_segment_bytes:
                        raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
                    sha = hashlib.sha256(raw).hexdigest()
                    pending[sha + ".json"] = raw
                    segments.append({"sha256": sha, "count": len(chunk), "bytes": len(raw)})
                entry = {"path": list(path), "kind": kind, "count": len(value), "segments": segments}
            active = self._replace(active, path, None)
            entries.append(entry)
        manifest = {"schema_version": 2, "policy": policy, "archive_policy": self.policy.json(),
                    "state": active, "archives": entries}
        raw = encode(manifest)
        if len(raw) > self.policy.max_active_bytes:
            raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
        # Clock/cancel bookkeeping may still publish a bounded active cut when
        # an interrupted writer has exhausted disk allocation with orphans.
        # New-risk callers separately check the archive's total allocation.
        usage = self._usage() if pending else {}
        for name, segment in pending.items():
            if name in usage:
                path = self.directory / name
                if read_limited(path, self.policy.max_segment_bytes) != segment:
                    raise ValueError("ARCHIVE_SEGMENT_CHANGED")
            else:
                usage[name] = len(segment)
        if len(usage) > self.policy.max_segments or sum(usage.values()) > self.policy.max_archive_bytes:
            raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
        return raw, pending

    @staticmethod
    def _sync(directory):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def publish_segments(self, pending):
        # Parent fsync anchors the directory itself before a manifest refers
        # to it. Segment fsync and directory fsync precede manifest replacement.
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink():
            raise ValueError("ARCHIVE_DIRECTORY_INVALID")
        self._sync(self.directory.parent)
        for name, raw in pending.items():
            target = self.directory / name
            if target.exists():
                if target.is_symlink() or read_limited(target, self.policy.max_segment_bytes) != raw:
                    raise ValueError("ARCHIVE_SEGMENT_CHANGED")
                continue
            fd, temporary = tempfile.mkstemp(prefix=".segment-", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        self._sync(self.directory)

    def read(self, raw, *, freeze):
        """Validate exact manifest/segment bytes, never trust file timestamps."""
        if len(raw) > self.policy.max_active_bytes:
            raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
        manifest = decode(raw)
        if (set(manifest) != {"schema_version", "policy", "archive_policy", "state", "archives"}
                or manifest["schema_version"] != 2 or manifest["archive_policy"] != self.policy.json()
                or not isinstance(manifest["state"], dict) or not isinstance(manifest["archives"], list)
                or len(manifest["archives"]) != len(self.policy.paths)):
            raise ValueError("ARCHIVE_POLICY_MISMATCH")
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise ValueError("ARCHIVE_DIRECTORY_INVALID")
        cached = self._image
        images = {}
        loaded, total_bytes = {}, 0
        for entry, path in zip(manifest["archives"], self.policy.paths):
            if (not isinstance(entry, dict) or set(entry) != {"path", "kind", "count", "segments"}
                    or entry["path"] != list(path) or entry["kind"] not in ("map", "list")
                    or type(entry["count"]) is not int or entry["count"] < 0
                    or not isinstance(entry["segments"], list)
                    or len(entry["segments"]) > self.policy.max_segments
                    or self._branch(manifest["state"], path) is not None):
                raise ValueError("ARCHIVE_COVERAGE_INVALID")
            count = 0
            for index, segment in enumerate(entry["segments"]):
                if (not isinstance(segment, dict) or set(segment) != {"sha256", "count", "bytes"}
                        or type(segment["sha256"]) is not str or len(segment["sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in segment["sha256"])
                        or type(segment["count"]) is not int or not 0 < segment["count"] <= self.policy.records_per_segment
                        or index < len(entry["segments"]) - 1 and segment["count"] != self.policy.records_per_segment
                        or type(segment["bytes"]) is not int or not 0 < segment["bytes"] <= self.policy.max_segment_bytes):
                    raise ValueError("ARCHIVE_SEGMENT_INVALID")
                name = segment["sha256"] + ".json"
                target = self.directory / name
                if target.is_symlink() or not target.is_file():
                    raise ValueError("ARCHIVE_SEGMENT_UNAVAILABLE")
                if name not in loaded:
                    total_bytes += segment["bytes"]
                    if len(loaded) >= self.policy.max_segments or total_bytes > self.policy.max_archive_bytes:
                        raise ValueError("ARCHIVE_CAPACITY_EXCEEDED")
                    loaded[name] = read_limited(target, segment["bytes"])
                blob = loaded[name]
                # The existing image contains independently hash-checked bytes.
                # Re-read every file, but exact bytes plus the same descriptor
                # can reuse that proof across active-only manifest changes.
                # A new descriptor (including a new digest/count) must validate
                # from scratch even if its bytes equal an older segment.
                previous = None if cached is None else cached[2].get(path)
                prior_segments = () if previous is None else previous[1]["segments"]
                unchanged = (index < len(prior_segments) and prior_segments[index] == segment
                             and cached[1].get((path, index)) == blob)
                if (len(blob) != segment["bytes"]
                        or not unchanged and hashlib.sha256(blob).hexdigest() != segment["sha256"]):
                    raise ValueError("ARCHIVE_SEGMENT_CHANGED")
                images[(path, index)] = blob
                count += segment["count"]
            if count != entry["count"]:
                raise ValueError("ARCHIVE_COVERAGE_INVALID")
        if cached is not None and cached[0] == raw and cached[1] == images:
            return cached[3]
        state, branches = manifest["state"], {}
        for entry, path in zip(manifest["archives"], self.policy.paths):
            if (cached is not None and path in cached[2] and cached[2][path][1] == entry
                    and all(cached[1].get((path, i)) == images[(path, i)] for i in range(len(entry["segments"])))):
                result = cached[2][path][0]
                branches[path] = (result, entry)
                state = self._replace(state, path, result)
                continue
            result = {} if entry["kind"] == "map" else []
            for index, segment in enumerate(entry["segments"]):
                chunk = decode(images[(path, index)])
                if type(chunk) is not type(result) or len(chunk) != segment["count"]:
                    raise ValueError("ARCHIVE_COVERAGE_INVALID")
                if isinstance(result, dict):
                    if result.keys() & chunk.keys():
                        raise ValueError("ARCHIVE_DUPLICATE_KEY")
                    result.update(chunk)
                else:
                    result.extend(chunk)
            result = freeze(result)
            branches[path] = (result, entry)
            state = self._replace(state, path, result)
        state = freeze(state)
        self._image = (raw, images, branches, state)
        return state

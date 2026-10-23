"""Explicit WAL/spot journal partitions; no pruning or implicit live migration.

Active manifests are byte bounded. Terminal maps use segment-sized lazy views;
permission verification still reads every segment and is history dependent.
The owning journal must hold its writer lock during migration or publication.
"""
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy, CheckpointArchive, encode
from hummingbot.strategy_v2.life_liquidity.lazy_archive import PartitionMap, open_maps, verify_maps
from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze


class JournalArchive:
    def __init__(self, path, policy, *, kind, limits=None):
        paths = (("settled",),) if kind == "wal" else tuple(
            ("settled", key) for key in ("reservations", "trades", "account_events"))
        if kind not in ("wal", "reservations") or not isinstance(policy, ArchivePolicy) or policy.paths != paths:
            raise ValueError("ARCHIVE_JOURNAL_POLICY_INVALID")
        self.path = Path(path)
        self.archive = CheckpointArchive(self.path, policy)
        self.binding = {"journal": kind, "limits": limits}
        self.kind = kind
        self.raw = None
        self.uncertain = False
        self._identity = (self.path, self.archive, policy, encode(self.binding))

    def _bound(self):
        if (self.uncertain or (self.path, self.archive, self.archive.policy, encode(self.binding)) != self._identity
                or self.archive.directory != self.path.with_name(self.path.name + ".segments")
                or self.path.is_symlink() or not self.path.is_file()):
            raise ValueError("ARCHIVE_JOURNAL_UNCERTAIN")

    def _split(self, data):
        if self.kind == "wal":
            active, settled = {}, {}
            for key, row in data["records"].items():
                target = settled if row["state"] in ("TERMINAL", "ABORTED_BEFORE_SEND") else active
                target[key] = row
            return {"active": active, "settled": settled}
        active, settled = ({key: {} for key in ("reservations", "trades", "account_events")} for _ in range(2))
        for key, row in data["reservations"].items():
            (settled if row["state"] == "TERMINAL" else active)["reservations"][key] = row
        for key, row in data["trades"].items():
            (settled if row[0] in settled["reservations"] else active)["trades"][key] = row
        for key, row in data.get("account_events", {}).items():
            target = active if row[0] == "FEE" and key[4:] in active["trades"] else settled
            target["account_events"][key] = row
        return {"active": active, "settled": settled,
                "life_balance": data["life_balance"], "usdt_balance": data["usdt_balance"]}

    def _join(self, state, *, settled_verified=False):
        return self.join_state(state, kind=self.kind, limits=self.binding['limits'], settled_verified=settled_verified)

    @staticmethod
    def join_state(state, *, kind, limits=None, settled_verified=False):
        """Decode a partition without opening a journal or granting authority."""
        if kind not in ('wal', 'reservations'):
            raise ValueError('ARCHIVE_JOURNAL_POLICY_INVALID')
        try:
            active, settled = state["active"], state["settled"]
            if kind == "wal":
                if (set(state) != {"active", "settled"}
                        or not isinstance(active, Mapping) or not isinstance(settled, Mapping)
                        or any(key in settled for key in active)
                        or any(row["state"] in ("TERMINAL", "ABORTED_BEFORE_SEND") for row in active.values())
                        or not settled_verified and any(row["state"] not in ("TERMINAL", "ABORTED_BEFORE_SEND") for row in settled.values())):
                    raise ValueError("ARCHIVE_PARTITION_INVALID")
                return {"schema_version": 1, "records": PartitionMap(active, settled)}
            keys = {"reservations", "trades", "account_events"}
            if (set(state) != {"active", "settled", "life_balance", "usdt_balance"}
                    or set(active) != keys or set(settled) != keys
                    or any(not isinstance(part[key], Mapping) for part in (active, settled) for key in keys)
                    or any(any(k in settled[key] for k in active[key]) for key in keys)):
                raise ValueError("ARCHIVE_PARTITION_INVALID")
            data = {"schema_version": 2, "limits": limits,
                    "life_balance": state["life_balance"], "usdt_balance": state["usdt_balance"],
                    **{key: PartitionMap(active[key], settled[key]) for key in keys}}
            for branch, terminal in ((active, False), (settled, True)):
                if terminal and settled_verified:
                    continue
                if any((row['state'] == 'TERMINAL') != terminal for row in branch['reservations'].values()):
                    raise ValueError('ARCHIVE_PARTITION_INVALID')
                for row in branch['trades'].values():
                    if (row[0] in active['reservations']) == terminal:
                        raise ValueError('ARCHIVE_PARTITION_INVALID')
                for key, row in branch['account_events'].items():
                    if (row[0] == 'FEE' and (key[4:] in active['trades']) == terminal
                            or row[0] != 'FEE' and not terminal):
                        raise ValueError('ARCHIVE_PARTITION_INVALID')
            return data
        except (KeyError, TypeError, AttributeError, IndexError) as exc:
            raise ValueError("ARCHIVE_PARTITION_INVALID") from exc

    def read(self):
        self._bound()
        raw = self.archive.read_manifest(self.path)
        cached = getattr(self, '_lazy_cut', None)
        if cached is None or cached[0] != raw:
            manifest, state = open_maps(self.archive, raw, indexes=(
                {('settled',): 'client_order_id'} if self.kind == 'wal' else None))
            if manifest.get('policy') != self.binding:
                raise ValueError('ARCHIVE_JOURNAL_POLICY_MISMATCH')
            data = MappingProxyType(self._join(state))
            cached = (raw, manifest, state, data)
            self._lazy_cut = cached
            self.archive._lazy_branches = {
                path: (self.archive._branch(state, path), entry)
                for path, entry in zip(self.archive.policy.paths, manifest['archives'])}
        else:
            verify_maps(self.archive, cached[2])
        if self.archive.read_manifest(self.path) != raw:
            raise ValueError('ARCHIVE_JOURNAL_CHANGED')
        return raw, cached[3]

    def verify(self):
        raw, data = self.read()
        if self.raw != raw:
            raise ValueError("ARCHIVE_JOURNAL_STALE")
        return data

    def verified_copy(self):
        """Detach reader bookkeeping while sharing only immutable verified cuts."""
        self.verify()
        copied = type(self)(self.path, self.archive.policy, kind=self.kind, limits=self.binding['limits'])
        copied.raw, copied._lazy_cut = self.raw, self._lazy_cut
        copied.archive._lazy_branches = dict(self.archive._lazy_branches)
        return copied

    def publish(self, data, *, expected):
        if self.uncertain:
            raise ValueError("ARCHIVE_JOURNAL_UNCERTAIN")
        if self.raw is not None:
            self.verify()
        if self.path.is_symlink() or self.path.read_bytes() != expected:
            raise ValueError("ARCHIVE_JOURNAL_STALE")
        state = self._split(data)
        self._join(state)
        raw, pending = self.archive.prepare(state, self.binding)
        self.archive.publish_segments(pending)
        self._publish_root(raw)

    def publish_active(self, active, *, expected):
        """Keep every terminal descriptor intact while checkpointing pending state.

        No financial balance or settled evidence changes on this path. The owner
        validates its typed state and holds the writer lock across this CAS.
        """
        self.verify()
        if self.raw != expected:
            raise ValueError('ARCHIVE_JOURNAL_STALE')
        _, manifest, old_state, _ = self._lazy_cut
        old_active = old_state['active']
        branches = (None,) if self.kind == 'wal' else ('reservations', 'trades', 'account_events')
        for branch in branches:
            before = old_active if branch is None else old_active[branch]
            after = active if branch is None else active[branch]
            if any(key not in after for key in before):
                raise ValueError('ARCHIVE_PARTITION_INVALID')
        state = _freeze({**old_state, 'active': active})
        data = MappingProxyType(self._join(state, settled_verified=True))
        manifest = _freeze({**manifest, 'state': {**manifest['state'], 'active': active}})
        raw = encode(manifest)
        if len(raw) > self.archive.policy.max_active_bytes:
            raise ValueError('ARCHIVE_CAPACITY_EXCEEDED')
        if self.path.is_symlink() or self.path.read_bytes() != expected:
            raise ValueError('ARCHIVE_JOURNAL_STALE')
        self._publish_root(raw, candidate=(raw, manifest, state, data))

    def _publish_root(self, raw, *, candidate=None):
        fd, temporary = tempfile.mkstemp(prefix="." + self.path.name, dir=self.path.parent)
        attempted = False
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            attempted = True
            os.replace(temporary, self.path)
            self.archive._sync(self.path.parent)
            self.raw = raw
            self.archive._image = None
            if candidate is not None:
                self._lazy_cut = candidate
            self.verify()
        except Exception:
            if attempted:
                self.uncertain = True
            raise
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

"""Bounded immutable runtime artifacts for cold handoff-source readback."""
import base64
import hashlib
import json
import os
import tempfile

from hummingbot.strategy_v2.life_liquidity.storage.configuration_handoff import FinancialConfigurationSource
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    StateCut,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    thaw,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recorder_source import RecorderSource
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.service_source import FileServiceSource


def pack_source(source):
    def b64(raw):
        return base64.b64encode(raw).decode()
    return encode({'format': 1, 'core': {'cut': source.core.cut.json(), 'checkpoint': b64(source.core.checkpoint_bytes),
                                         'journals': [[name, b64(raw)] for name, raw in source.core.journal_images],
                                         'facts': source.core.facts.json()},
                   'service_json': source.service_json, 'ancillary': [[name, b64(raw)] for name, raw in source.ancillary_images],
                   'recorder': {'cut': source.recorder.cut.json(), 'controller_id': source.recorder.controller_id,
                                'rows_json': source.recorder.rows_json}})


def unpack_source(raw):
    try:
        body = json.loads(raw)
        if set(body) != {'format', 'core', 'service_json', 'ancillary', 'recorder'} or body['format'] != 1:
            raise ValueError()
        core, recorder = body['core'], body['recorder']

        def decode(value):
            return base64.b64decode(value, validate=True)
        source = FinancialConfigurationSource(FinancialCut.from_json(core['cut']), decode(core['checkpoint']),
                                              tuple((name, decode(value)) for name, value in core['journals']),
                                              SpotFinancialRecoveryFacts.from_json(core['facts']))
        return FileServiceSource(source, body['service_json'], tuple((name, decode(value)) for name, value in body['ancillary']),
                                 RecorderSource(StateCut.from_json(recorder['cut']), recorder['controller_id'], recorder['rows_json']))
    except (ValueError, KeyError, TypeError, StorageError):
        raise StorageUnavailable('HANDOFF_ARTIFACT_INVALID') from None


def artifact_name(command, approval_body):
    return fingerprint({'command': command.json(), 'approval': thaw(approval_body)}) + '.json'


def bound(command):
    return 4 * command.max_bytes + 65536  # JSON/base64 overhead, not a live financial default.


def read_bytes(path, maximum):
    if not path.is_file() or path.is_symlink():
        raise StorageUnavailable('HANDOFF_ARTIFACT_UNAVAILABLE')
    with path.open('rb') as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise StorageUnavailable('HANDOFF_ARTIFACT_CAPACITY_EXCEEDED')
    return raw


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def persist_source(store, command, approval, source):
    store.assert_worker_thread()
    folder = store._path.parent / 'handoff_sources'
    if folder.is_symlink() or folder.exists() and not folder.is_dir():
        raise StorageUnavailable('HANDOFF_ARTIFACT_DIRECTORY_UNSAFE')
    if not folder.exists():
        folder.mkdir(mode=0o700)
        sync_dir(folder.parent)
    if command.source_format == 'STOPPED_FILE_SERVICE_V1':
        from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import pack_stopped_source
        raw = pack_stopped_source(source, max_bytes=command.max_bytes, max_rows=command.max_rows)
    else:
        raw = pack_source(source)
    name = artifact_name(command, approval.body())
    if len(raw) > bound(command):
        raise StorageUnavailable('HANDOFF_ARTIFACT_CAPACITY_EXCEEDED')
    destination = folder / name
    if destination.exists() or destination.is_symlink():
        if read_bytes(destination, bound(command)) != raw:
            raise StorageUnavailable('HANDOFF_ARTIFACT_CONFLICT')
    else:
        with os.scandir(folder) as entries:
            for count, entry in enumerate(entries, 1):
                if count >= store.limits.max_operations or entry.is_symlink() or not entry.is_file():
                    raise StorageUnavailable('HANDOFF_ARTIFACT_DIRECTORY_CAPACITY_OR_ORIGIN_INVALID')
        descriptor, temporary = tempfile.mkstemp(prefix='.handoff-', dir=folder)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, destination)  # Publish without overwriting an earlier image.
            except FileExistsError:
                if read_bytes(destination, bound(command)) != raw:
                    raise StorageUnavailable('HANDOFF_ARTIFACT_CONFLICT')
            os.unlink(temporary)
            sync_dir(folder)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    with destination.open('rb') as handle:
        os.fsync(handle.fileno())
    sync_dir(folder)  # Also finish a previously interrupted directory publication.
    return {'name': name, 'sha256': hashlib.sha256(raw).hexdigest()}


def load_source(store, command, descriptor):
    store.assert_worker_thread()
    record = store._read().resources['control_operations'][command.operation_id]
    folder = store._path.parent / 'handoff_sources'
    if folder.is_symlink() or descriptor['name'] != artifact_name(command, record['approval']):
        raise StorageUnavailable('HANDOFF_ARTIFACT_ORIGIN_INVALID')
    raw = read_bytes(folder / descriptor['name'], bound(command))
    if hashlib.sha256(raw).hexdigest() != descriptor['sha256']:
        raise StorageUnavailable('HANDOFF_ARTIFACT_INVALID')
    if command.source_format == 'STOPPED_FILE_SERVICE_V1':
        from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import unpack_stopped_source
        source = unpack_stopped_source(raw, expected_sha256=descriptor['sha256'], max_bytes=command.max_bytes, max_rows=command.max_rows)
        core = source.base.core
    else:
        source = unpack_source(raw)
        core = source.core
        if pack_source(source) != raw:
            raise StorageUnavailable('HANDOFF_ARTIFACT_ORIGIN_INVALID')
    if (source.content_digest != command.source_digest or core.cut.json() != thaw(record['cut'])
            or core.facts.json() != thaw(record['facts'])):
        raise StorageUnavailable('HANDOFF_ARTIFACT_ORIGIN_INVALID')
    return source

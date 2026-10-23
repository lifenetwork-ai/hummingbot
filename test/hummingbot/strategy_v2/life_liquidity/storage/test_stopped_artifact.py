"""Cold, bounded complete File bytes; no source retirement or DB activation."""
import asyncio
import base64
import hashlib
import json
import os
import sys
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_handoff_source import LIMITS, staged
from test.hummingbot.strategy_v2.life_liquidity.storage.test_stopped_service_source import joint_staged

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode
from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import (
    load_stopped_artifact,
    pack_stopped_source,
    publish_stopped_artifact,
    unpack_stopped_source,
)


@pytest.mark.asyncio
@pytest.mark.parametrize('segmented', [False, True])
async def test_ordinary_and_segmented_source_roundtrip_after_service_close(tmp_path, segmented):
    from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
    service = await joint_staged(tmp_path) if segmented else (await staged(tmp_path))[0]
    try:
        if segmented:
            journal = service.joint._recovery.capital.journal
            await service.store._call(journal.migrate_archive, capacity())
        source = await service.control.stopped_postgres_source(**LIMITS)
        raw = pack_stopped_source(source, **LIMITS)
    finally:
        await service.close()
    sha = hashlib.sha256(raw).hexdigest()
    path = tmp_path / 'stopped-source.json'
    assert publish_stopped_artifact(path, source, **LIMITS) == sha
    assert publish_stopped_artifact(path, source, **LIMITS) == sha
    assert load_stopped_artifact(path, expected_sha256=sha, **LIMITS) == source
    assert unpack_stopped_source(raw, expected_sha256=sha, **LIMITS) == source
    # Outer digest includes facts, not only the inner journal/cut fingerprint.
    body = json.loads(raw)
    base = json.loads(base64.b64decode(body['base']))
    base['core']['facts']['observed_at'] = '2030-01-01T00:00:00+00:00'
    body['base'] = base64.b64encode(encode(base)).decode()
    with pytest.raises(StorageError, match='DIGEST'):
        unpack_stopped_source(encode(body), expected_sha256=sha, **LIMITS)
    path.write_bytes(b'partial-crash-file')
    with pytest.raises(StorageError):
        publish_stopped_artifact(path, source, **LIMITS)
    assert path.read_bytes() == b'partial-crash-file'


@pytest.mark.asyncio
async def test_artifact_bounds_corruption_and_symlinks_never_return_partial_source(tmp_path):
    service, _ = await staged(tmp_path)
    try:
        source = await service.control.stopped_postgres_source(**LIMITS)
    finally:
        await service.close()
    raw = pack_stopped_source(source, **LIMITS)
    sha = hashlib.sha256(raw).hexdigest()
    with pytest.raises(StorageError, match='CAPACITY'):
        unpack_stopped_source(raw, expected_sha256=sha, max_bytes=1, max_rows=128)
    with pytest.raises(StorageError, match='CAPACITY'):
        pack_stopped_source(source, max_bytes=1, max_rows=128)
    body = json.loads(raw)
    body['archives'] = [dict(name='ancillary.unknown', policy={}, root='', segments=[])]
    broken = encode(body)
    with pytest.raises(StorageError):
        unpack_stopped_source(broken, expected_sha256=hashlib.sha256(broken).hexdigest(), **LIMITS)
    real, link = tmp_path / 'source.json', tmp_path / 'link.json'
    publish_stopped_artifact(real, source, **LIMITS)
    link.symlink_to(real)
    with pytest.raises(StorageError):
        load_stopped_artifact(link, expected_sha256=sha, **LIMITS)
    with pytest.raises(StorageError):
        publish_stopped_artifact(link, source, **LIMITS)


@pytest.mark.asyncio
async def test_killed_publisher_leaves_only_an_orphan_and_cold_retry_is_exact(tmp_path):
    service, _ = await staged(tmp_path)
    try:
        source = await service.control.stopped_postgres_source(**LIMITS)
    finally:
        await service.close()
    seed, target = tmp_path / 'input.json', tmp_path / 'published.json'
    sha = publish_stopped_artifact(seed, source, **LIMITS)
    code = '''
import os, sys
from pathlib import Path
from hummingbot.strategy_v2.life_liquidity.storage import stopped_artifact as a
limits = dict(max_bytes=8388608, max_rows=128)
source = a.load_stopped_artifact(Path(sys.argv[1]), expected_sha256=sys.argv[3], **limits)
def killed(*args, **kwargs):
    os._exit(91)
a.os.link = killed
a.publish_stopped_artifact(Path(sys.argv[2]), source, **limits)
'''
    env = {key: os.environ[key] for key in ('PATH', 'PYTHONPATH') if key in os.environ}
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', code, str(seed), str(target), sha,
                                                   env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        _, errors = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 91, errors.decode()
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    assert not target.exists()
    orphans = tuple(tmp_path.glob('.stopped-*'))
    assert len(orphans) == 1
    assert publish_stopped_artifact(target, source, **LIMITS) == sha
    assert load_stopped_artifact(target, expected_sha256=sha, **LIMITS) == source
    assert orphans[0].exists()  # Never adopt or erase unidentified prior work.

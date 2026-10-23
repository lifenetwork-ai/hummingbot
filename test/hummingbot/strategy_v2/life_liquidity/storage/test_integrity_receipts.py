"""Independent diagnostic attestations never become trading permissions."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, encode
from hummingbot.strategy_v2.life_liquidity.storage.integrity_receipts import (
    IntegrityMonitor,
    IntegrityPolicy,
    IntegrityReceipt,
)

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def fixture():
    key = Ed25519PrivateKey.generate()
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'main', uuid4(), 1, 'a' * 64, 'b' * 64)
    policy = IntegrityPolicy('offline-auditor', key.public_key().public_bytes_raw(), uuid4(), uuid4(), binding,
                             audit_max_age_ms=60000, backup_max_age_ms=600000, restore_max_age_ms=600000)
    return key, policy


def signed(key, policy, kind='AUDIT', **changes):
    body = dict(protocol='LIFE_OFFLINE_INTEGRITY_V1', publisher_id=policy.publisher_id,
                account_id=str(policy.account_id), database_lineage=str(policy.database_lineage), binding=policy.binding.json(),
                kind=kind, observed_at=NOW.isoformat(), result='PASSED', artifact_digest='c' * 64)
    body.update(changes)
    raw = encode(body)
    return IntegrityReceipt(raw.decode(), key.sign(raw).hex())


def test_scope_signature_freshness_and_result_are_required():
    key, policy = fixture()
    receipt = signed(key, policy)
    policy.verify(receipt, now=NOW)
    for bad in (replace(receipt, signature='0' * 128), signed(Ed25519PrivateKey.generate(), policy),
                signed(key, policy, result='FAILED'), signed(key, policy, account_id=str(uuid4())),
                signed(key, policy, database_lineage=str(uuid4())), signed(key, policy, publisher_id='other'),
                signed(key, policy, protocol='PRODUCTION'), signed(key, policy, observed_at=(NOW + timedelta(seconds=1)).isoformat())):
        with pytest.raises(ValueError):
            policy.verify(bad, now=NOW)
    with pytest.raises(ValueError):
        policy.verify(receipt, now=NOW + timedelta(seconds=60, microseconds=1))


@pytest.mark.asyncio
async def test_health_ages_without_a_new_poll_and_missing_evidence_is_unknown():
    key, policy = fixture()
    now = [NOW]

    async def read():
        return (signed(key, policy), signed(key, policy, 'BACKUP'))
    monitor = IntegrityMonitor(policy, read, timeout_ms=50, clock=lambda: now[0])
    assert monitor.status()['audit_health'] == 'UNVERIFIED'
    await monitor.sample()
    assert monitor.status()['audit_health'] == monitor.status()['backup_health'] == 'VERIFIED'
    assert monitor.status()['recovery_health'] == 'UNVERIFIED'
    assert monitor.status()['trading_permission'] is False
    now[0] += timedelta(seconds=61)
    assert monitor.status()['audit_health'] == 'UNVERIFIED'
    assert monitor.status()['backup_health'] == 'VERIFIED'
    await monitor.close()
    assert monitor.status()['backup_health'] == 'UNVERIFIED'


@pytest.mark.asyncio
async def test_failed_or_rolled_back_publication_does_not_refresh_health():
    key, policy = fixture()
    packet = [(signed(key, policy),)]

    async def read():
        if isinstance(packet[0], Exception):
            raise packet[0]
        return packet[0]
    monitor = IntegrityMonitor(policy, read, timeout_ms=50, clock=lambda: NOW + timedelta(seconds=1))
    await monitor.sample()
    assert monitor.status()['audit_health'] == 'VERIFIED'
    for payload in (RuntimeError('secret'), (signed(key, policy, observed_at=(NOW - timedelta(seconds=1)).isoformat()),),
                    (signed(key, policy, artifact_digest='d' * 64),), (signed(key, policy),) * 2):
        packet[0] = payload
        await monitor.sample()
        assert monitor.status()['audit_health'] == 'UNVERIFIED'
        assert 'secret' not in str(monitor.status())
    await monitor.close()


@pytest.mark.asyncio
async def test_timed_out_provider_keeps_single_physical_slot_until_joined():
    key, policy = fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def blocked():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return (signed(key, policy),)
    monitor = IntegrityMonitor(policy, blocked, timeout_ms=10, clock=lambda: NOW)
    await monitor.sample()
    assert entered.is_set()
    for _ in range(5):
        await monitor.sample()
    assert calls == 1 and monitor.status()['audit_health'] == 'UNVERIFIED'
    closing = asyncio.create_task(monitor.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await closing
    assert monitor.status()['audit_health'] == 'UNVERIFIED'


@pytest.mark.asyncio
async def test_provider_cancellation_is_unavailable_but_caller_cancellation_is_preserved():
    _, policy = fixture()

    async def cancelled():
        raise asyncio.CancelledError()
    monitor = IntegrityMonitor(policy, cancelled, timeout_ms=50, clock=lambda: NOW)
    await monitor.sample()
    assert monitor.status()['state'] == 'UNAVAILABLE'
    await monitor.close()

    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()
        return ()
    monitor = IntegrityMonitor(policy, blocked, timeout_ms=1000, clock=lambda: NOW)
    task = asyncio.create_task(monitor.sample())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    closing = asyncio.create_task(monitor.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await closing


@pytest.mark.asyncio
async def test_worker_cannot_report_stopped_before_original_integrity_io_joins(capsys):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_worker_supervisor import config
    from types import SimpleNamespace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.worker import WorkerSupervisor

    _, policy = fixture()
    entered, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()
        return ()

    async def launch(**kwargs):
        await kwargs['on_started'](SimpleNamespace(revoke=lambda: None, runtime=SimpleNamespace(healthy=True), _trading_ready=lambda: False))
        started.set()
        await kwargs['stop'].wait()
    supervisor = WorkerSupervisor(config(), launch=launch)
    supervisor._integrity_monitor = IntegrityMonitor(policy, blocked, timeout_ms=10, clock=lambda: NOW)
    await supervisor._integrity_monitor.sample()
    assert entered.is_set()
    task = asyncio.create_task(supervisor.run())
    await started.wait()
    supervisor.request_stop()
    for _ in range(10):
        await asyncio.sleep(0)
    try:
        assert not task.done()
        assert '"event":"STOPPED"' not in capsys.readouterr().out
    finally:
        release.set()
        await task
    assert '"event":"STOPPED"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_worker_binds_public_monitor_to_exact_native_namespace():
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_postgres_runtime_config import configuration
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_worker_supervisor import config
    from types import SimpleNamespace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.worker import WorkerSupervisor

    key, policy = fixture()
    cfg = configuration()
    policy = replace(policy, account_id=cfg.account_id, database_lineage=cfg.database_lineage, binding=cfg.binding)

    async def read():
        return tuple(signed(key, policy, kind) for kind in ('AUDIT', 'BACKUP', 'RESTORE'))
    monitor = IntegrityMonitor(policy, read, timeout_ms=1000, clock=lambda: NOW)
    service = SimpleNamespace(runtime=SimpleNamespace(config=cfg, healthy=True), limits=SimpleNamespace(max_rows=16),
                              _trading_ready=lambda: False, _integrity_monitor=monitor)
    supervisor = WorkerSupervisor(config())
    await supervisor._started(service)
    try:
        await monitor.sample()
        state = supervisor.status()
        assert not state['trading_permission'] and not state['local_trading_ready']
        assert state['financial_metrics']['audit_health'] == state['financial_metrics']['backup_health'] == state['financial_metrics']['recovery_health'] == 'VERIFIED'
        assert not state['integrity_metrics']['trading_permission']
        service._integrity_monitor = IntegrityMonitor(replace(policy, database_lineage=uuid4()), read, timeout_ms=1000)
        rejected = WorkerSupervisor(config())
        try:
            with pytest.raises(ValueError, match='WORKER_INTEGRITY_SCOPE_INVALID'):
                await rejected._started(service)
        finally:
            await rejected._financial_monitor.close()
    finally:
        await supervisor._financial_monitor.close()
        await monitor.close()

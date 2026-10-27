from copy import deepcopy
from random import Random
from test.hummingbot.core.test_mm_until_stopped import anchors, catalogue, request
from types import SimpleNamespace

import pytest
from aiohttp import BasicAuth, web
from aiohttp.test_utils import TestClient, TestServer
from sqlalchemy import update

from hummingbot.cli.admin_api import ADMIN_PREFIX, AdminAPI, admin_middleware
from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.mm_session import MMSession
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.admin_store import AdminStore
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore, ControlConflict
from hummingbot.model.db_migration.postgres.runner import bootstrap_postgres


def client(store, cfg):
    settings = AdminSettings(True, 'operator', 'test-password')
    app = web.Application(middlewares=[admin_middleware(settings)])
    AdminAPI(SimpleNamespace(store=store, revision='test'), settings, catalogue=cfg).register(app)
    return TestClient(TestServer(app), headers={'Authorization': BasicAuth('operator', 'test-password').encode()})


@pytest.mark.asyncio
async def test_http_until_stopped_permission_pinned_restart_retry_and_switch_to_timed(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    enabled, disabled = await catalogue(), await catalogue(False)
    owner = BotOwnership(pg_engine, 'hummingbot')
    assert owner.try_acquire()
    payload = dict(action='start', expected_version=0, expected_catalogue_hash=enabled.catalogue_hash(),
                   market_mode='both', selection='random', duration_mode='until_stopped')
    try:
        async with client(store, enabled) as http, client(store, disabled) as revised:
            assert (await (await http.get(ADMIN_PREFIX + '/profiles')).json())['duration_modes'] == ['timed', 'until_stopped']
            assert (await (await revised.get(ADMIN_PREFIX + '/profiles')).json())['duration_modes'] == ['timed']
            denied = await revised.post(
                ADMIN_PREFIX + '/commands', json={**payload, 'expected_catalogue_hash': disabled.catalogue_hash()},
                headers={'Idempotency-Key': 'disabled'})
            assert denied.status == 422 and (await denied.json())['code'] == 'duration_mode_not_allowed'
            assert store.read()['control_version'] == 0
            response = await http.post(ADMIN_PREFIX + '/commands', json=payload, headers={'Idempotency-Key': 'continuous'})
            receipt = await response.json()
            assert response.status == 202
            session = store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=1,
                                              session_id=receipt['session_id'], generation=0,
                                              change=lambda current, now: current.ready(now=now, anchors=anchors(current)))
            assert session.expires_at is None and session.step_expires_at is not None
            owner.mm_ready_check = lambda: True
            owner._check_mm_authority(store.read(), (session.id, 0))
            restored = MMSession.model_validate(BotStateStore(pg_engine, 'hummingbot').read()['mm_session'])
            assert restored == session
            status = await (await revised.get(ADMIN_PREFIX + '/status')).json()
            assert status['session']['request']['duration_mode'] == 'until_stopped'
            assert status['session']['expires_at'] is None and status['time_remaining_seconds'] is None
            retry = await revised.post(ADMIN_PREFIX + '/commands', json=payload, headers={'Idempotency-Key': 'continuous'})
            assert retry.status == 202 and (await retry.json())['id'] == receipt['id']
            config = await (await revised.get(ADMIN_PREFIX + f'/sessions/{session.id}/configuration')).json()
            assert config['catalogue']['allow_until_stopped']
            switch = dict(action='switch', expected_version=1, session_id=session.id, market_mode='both',
                          selection='random', duration_mode='until_stopped')
            assert (await revised.post(ADMIN_PREFIX + '/commands', json=switch, headers={'Idempotency-Key': 'new-disabled'})).status == 422
            response = await revised.post(
                ADMIN_PREFIX + '/commands', json={**switch, 'duration_mode': 'timed', 'duration_seconds': 60},
                headers={'Idempotency-Key': 'switch-timed'})
            timed_receipt = await response.json()
            assert response.status == 202
            timed = store.update_mm_session(owner_uuid=owner.owner_uuid, owner_generation=owner.generation, version=2,
                                            session_id=timed_receipt['session_id'], generation=0,
                                            change=lambda current, now: current.ready(now=now, anchors=anchors(current)))
            assert timed.expires_at - timed.started_at == 60
            response = await revised.post(
                ADMIN_PREFIX + '/commands', json=dict(action='stop', expected_version=2, session_id=timed.id),
                headers={'Idempotency-Key': 'stop'})
            assert response.status == 202
            with pytest.raises(SubmissionBlocked):
                owner._check_mm_authority(store.read(), (timed.id, 0))
    finally:
        owner.release()


@pytest.mark.asyncio
async def test_pg_cold_owner_restore_skips_elapsed_random_windows_and_halt_still_blocks(pg_engine):
    bootstrap_postgres(pg_engine, auto_migrate=True)
    store = BotStateStore(pg_engine, 'hummingbot')
    store.ensure()
    cfg = await catalogue()
    cfg.random.profile_ids = ['sideways']
    admin = AdminStore(store)
    payload = dict(action='start', expected_version=0, market_mode='both', selection='random', duration_mode='until_stopped')
    receipt = admin.command('until', payload, catalogue=cfg, request=request())
    first = BotOwnership(pg_engine, 'hummingbot')
    assert first.try_acquire()
    session = store.update_mm_session(
        owner_uuid=first.owner_uuid, owner_generation=first.generation, version=1,
        session_id=receipt['session_id'], generation=0,
        change=lambda current, now: current.ready(now=now, anchors=anchors(current)))
    saved = deepcopy(store.read()['mm_session'])
    first.release()
    successor = BotOwnership(pg_engine, 'hummingbot')
    assert successor.try_acquire()
    try:
        assert store.read()['mm_session'] == saved
        # Simulate a long offline interval without sleeping or extending any clock.
        saved['started_at'] -= 86400 * 30
        saved['step_expires_at'] -= 86400 * 30
        with pg_engine.begin() as connection:
            connection.execute(update(BotState).values(mm_session=saved))
        advanced = store.update_mm_session(owner_uuid=successor.owner_uuid, owner_generation=successor.generation, version=1,
                                           session_id=session.id, generation=0,
                                           change=lambda current, now: current.advance(now=now, rng=Random(3)))
        assert advanced.step_index == 4320 and advanced.generation == 1 and advanced.expires_at is None
        ready = store.update_mm_session(owner_uuid=successor.owner_uuid, owner_generation=successor.generation, version=1,
                                        session_id=session.id, generation=1,
                                        change=lambda current, now: current.ready(now=now, anchors=anchors(current)))
        successor.mm_ready_check = lambda: True
        successor._check_mm_authority(store.read(), (session.id, 1))
        with pytest.raises(SubmissionBlocked):
            successor._check_mm_authority(store.read(), (session.id, 0))
        successor.require_recovery('test confirmed uncertainty')
        with pytest.raises(ControlConflict):
            store.update_mm_session(
                owner_uuid=successor.owner_uuid, owner_generation=successor.generation, version=1,
                session_id=ready.id, generation=1, change=lambda current, now: None)
        switch = {**payload, 'action': 'switch', 'expected_version': 1, 'session_id': session.id}
        with pytest.raises(ControlConflict, match='HALT'):
            admin.command('cannot-bypass', switch, catalogue=cfg, request=request())
        admin.command('stop', dict(action='stop', expected_version=1, session_id=session.id), catalogue=None)
        assert store.read()['recovery_state']['halt'] and store.read()['desired_state'] == 'stopped'
    finally:
        successor.release()

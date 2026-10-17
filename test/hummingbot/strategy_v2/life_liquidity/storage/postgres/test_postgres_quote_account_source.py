"""Account/policy capture through actual signed OKX reads; fake HTTP only."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import bridge
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource


async def source(t, *, uid_age_ms=1000, auth_age_ms=1000):
    return await PgQuoteAccountSource.create(
        t.journal, connector=t.connector, admission_source=t.facts, max_uid_age_ms=uid_age_ms,
        max_auth_age_ms=auth_age_ms, max_clock_skew_ms=100, max_pending=2, collection_timeout_ms=1000)


def account_http(t, *, uid='12345'):
    original = t.session.request
    t.connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    reads = []

    async def request(**kwargs):
        if kwargs['url'].endswith('/api/v5/account/config'):
            reads.append(kwargs)
            response = Response()

            async def body():
                return {'code': '0', 'data': [{'uid': uid}]}
            response.json = body
            return response
        return await original(**kwargs)
    t.session.request = request
    return reads


@pytest.mark.asyncio
async def test_policy_read_is_exact_scoped_and_historical_not_a_send_grant(database):
    t = await transport(database)
    try:
        value = await t.repo.quote_admission_policy(t.cfg.binding)
        assert value.policy.binding == t.cfg.binding and value.policy.tick_size == Decimal('.01')
        assert value.policy.max_fact_age_ms == value.policy.max_capture_age_ms == 1000
        assert value.cut == (await t.repo.snapshot(t.cfg.binding)).cut
        assert value.observed_at.tzinfo is not None
        with pytest.raises(StorageError, match='PG_SPOT_BINDING_MISMATCH'):
            await t.repo.quote_admission_policy(replace(t.cfg.binding, account_uid='999'))
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_same_credential_signed_uid_and_db_ages_are_bound_to_original_order(database):
    t = await transport(database)
    reads = account_http(t)
    try:
        captured = await source(t)
        guard = captured.guard(action=t.create, preparation=t.preparation)
        assert guard._age_limits.fact_ms == 1000
        assert (await t.place(guard=guard))[0] == '123'
        assert len(reads) == len(t.session.sent) == 1
        assert reads[0]['allow_redirects'] is False and reads[0]['data'] is None
        assert reads[0]['headers']['OK-ACCESS-KEY'] == t.session.sent[0]['headers']['OK-ACCESS-KEY']
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['wrong_uid', 'unsigned_uid', 'other_signed_key', 'changed_connector', 'wrong_endpoint',
                                   'signature', 'stale_signature'])
async def test_identity_or_credential_substitution_never_arms_or_sends_order(database, fault):
    t = await transport(database)
    reads = account_http(t, uid='999' if fault == 'wrong_uid' else '12345')
    try:
        captured = await source(t)
        guard = captured.guard(action=t.create, preparation=t.preparation)
        if fault == 'unsigned_uid':
            # The order itself is signed; only the UID HTTP request loses auth.
            original = t.assistant._authenticate

            async def auth(request):
                return request if request.url.endswith('/account/config') else await original(request)
            t.assistant._authenticate = auth
        elif fault == 'other_signed_key':
            t.assistant._auth.api_key = 'foreign-key'
        elif fault == 'changed_connector':
            t.connector.okx_api_key = 'foreign-key'
        elif fault == 'wrong_endpoint':
            t.connector._api_request_url = AsyncMock(return_value='https://foreign.test/api/v5/account/config')
        elif fault in ('signature', 'stale_signature'):
            original = t.assistant._authenticate

            async def auth(request):
                request = await original(request)
                if fault == 'signature':
                    request.headers['OK-ACCESS-SIGN'] = 'wrong-signature'
                else:
                    at = '2000-01-01T00:00:00.000Z'
                    request.headers['OK-ACCESS-TIMESTAMP'] = at
                    request.headers['OK-ACCESS-SIGN'] = t.connector._auth._generate_signature(
                        at, request.method.value, '/api/v5/trade/order', request.data)
                return request
            t.assistant._authenticate = auth
        with pytest.raises(StorageError):
            await t.place(guard=guard)
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
        assert len(reads) == (1 if fault == 'wrong_uid' else 0)
    finally:
        await t.close()


async def request_snapshot(t):
    values = []
    original = t.guard.authorize

    async def held(value):
        values.append(value)
        raise StorageError('SYNTHETIC_CAPTURE_ONLY')
    t.guard.authorize = held
    try:
        with pytest.raises(StorageError, match='SYNTHETIC_CAPTURE_ONLY'):
            await t.place()
    finally:
        t.guard.authorize = original
    return values[0]


@pytest.mark.asyncio
async def test_simultaneous_captures_coalesce_uid_http_but_recollect_admission(database):
    t = await transport(database)
    reads = account_http(t)
    try:
        captured = await source(t)
        snapshot = await request_snapshot(t)
        results = await asyncio.gather(captured.capture(snapshot), captured.capture(snapshot))
        assert len(reads) == 1 and len(results) == 2 and results[0] is not results[1]
        assert captured.pending_count == 0 and not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'precision'])
async def test_policy_import_is_required_and_decimals_do_not_round_through_json_float(database, fault):
    repo, cfg, owner, row = await bridge(database, prepare_policy=False, claim=False)
    try:
        if fault == 'missing':
            with pytest.raises(StorageError, match='PG_QUOTE_ADMISSION_POLICY_REQUIRED'):
                await repo.quote_admission_policy(cfg.binding)
        else:
            exact = Decimal('12345678901234567890.123456789012345678')
            tick = Decimal('.012345678901234567')
            await database[0].execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
                $1,$2,'liquidity_service',$3,0,$4,.01,.01,1000,1000,1000,1000)''',
                                      cfg.account_id, cfg.binding.policy_hash, exact, tick)
            value = await repo.quote_admission_policy(cfg.binding)
            assert value.policy.min_net_edge_bps == exact and value.policy.tick_size == tick
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['policy_changed', 'corrupt_history'])
async def test_changed_policy_or_unverified_history_cannot_become_account_source(database, fault):
    t = await transport(database)
    account_http(t)
    try:
        captured = await source(t)
        if fault == 'policy_changed':
            # Deliberate database-owner fault. Runtime has no UPDATE privilege.
            await database[0].execute('ALTER TABLE life_financial.spot_quote_admission_policies DISABLE TRIGGER immutable_history')
            await database[0].execute('UPDATE life_financial.spot_quote_admission_policies SET max_fact_age_ms=2000 WHERE account_id=$1', t.cfg.account_id)
            reason = 'PG_ACCOUNT_POLICY_CHANGED'
        else:
            await database[0].execute('ALTER TABLE life_financial.operations DISABLE TRIGGER immutable_history')
            await database[0].execute("UPDATE life_financial.operations SET receipt=receipt||'{\"intent_id\":\"foreign\"}'::jsonb WHERE account_id=$1 AND operation_id='operation1'", t.cfg.account_id)
            reason = 'PG_QUOTE_POLICY_UNVERIFIED'
        with pytest.raises(StorageError, match=reason):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert not t.session.sent
        assert await t.repo.lookup_quote_arm('operation1', t.cfg.binding) is None
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'foreign'])
async def test_account_capture_requires_original_bound_admission_facts(database, fault):
    t = await transport(database)
    account_http(t)
    try:
        captured = await source(t)

        async def bad():
            value = await t.facts()
            return None if fault == 'none' else replace(value, binding=replace(value.binding, account_uid='999'))
        captured._admission_source = bad
        with pytest.raises(StorageError, match='PG_ACCOUNT_ADMISSION_SOURCE_INVALID'):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_uid_timeout_does_not_cache_an_identity_or_release_financial_holds(database):
    t = await transport(database)
    account_http(t)
    try:
        captured = await source(t)
        captured.collection_timeout_ms = 20

        async def held(**kwargs):
            await asyncio.Event().wait()
        t.session.request = held
        with pytest.raises(StorageError, match='PG_ACCOUNT_SOURCE_UNAVAILABLE'):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert captured._uid_at is None and captured.pending_count == 0 and not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_returning_configured_uid_without_actual_request_guard_cannot_authenticate_source(database):
    t = await transport(database)
    try:
        captured = await source(t)
        t.connector.get_account_uid = AsyncMock(return_value=t.cfg.binding.account_uid)
        with pytest.raises(StorageError, match='PG_ACCOUNT_UID_TRANSPORT_UNQUALIFIED'):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert not t.session.sent and captured._uid_at is None
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [{'intent_id': 'foreign'}, {'operation_id': 'foreign'}, {'state': 'ACKED'},
                                    {'kind': 'SPOT_ARM'}, {'version': 999}, {'states': [['foreign', 'PREPARED']]},
                                    {'trading_permission': True}, {'unexpected': True}])
async def test_inconsistent_preparation_receipt_denies_source_and_historical_lookup(database, change):
    t = await transport(database)
    try:
        await database[0].execute('ALTER TABLE life_financial.operations DISABLE TRIGGER immutable_history')
        await database[0].execute('UPDATE life_financial.operations SET receipt=receipt||$2::jsonb WHERE account_id=$1 AND operation_id=$3',
                                  t.cfg.account_id, json.dumps(change), 'operation1')
        with pytest.raises(StorageError, match='PG_QUOTE_POLICY_UNVERIFIED'):
            await source(t)
        with pytest.raises(StorageError, match='PG_SPOT_RECEIPT_UNVERIFIED'):
            await t.repo.lookup_prepare('operation1', t.cfg.binding)
        assert (await t.repo.snapshot(t.cfg.binding)).recovery_required
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_receipt_corruption_at_physical_arm_commit_rolls_back_everything(database, monkeypatch):
    t = await transport(database)
    account_http(t)
    captured = await source(t)
    await database[0].execute('ALTER TABLE life_financial.operations DISABLE TRIGGER immutable_history')
    await database[0].execute('''CREATE FUNCTION life_financial.test_corrupt_prepare(p_account uuid) RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS
        $body$ UPDATE life_financial.operations SET receipt=receipt||'{"intent_id":"foreign"}'::jsonb
            WHERE account_id=p_account AND operation_id='operation1'; $body$;
        GRANT EXECUTE ON FUNCTION life_financial.test_corrupt_prepare(uuid) TO life_pg2_runtime''')
    original = t.repo._runtime._commit

    async def corrupt(tx):
        await tx._connection.execute('SELECT life_financial.test_corrupt_prepare($1)', t.cfg.account_id)
        await original(tx)
    monkeypatch.setattr(t.repo._runtime, '_commit', corrupt)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_SPOT_PREPARE_RECEIPTS_UNVERIFIED'):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut
        assert await t.repo.balance(t.cfg.binding) == before.balance
        assert await t.repo.lookup_quote_arm('operation1', t.cfg.binding) is None
        assert (await t.repo.lookup_prepare('operation1', t.cfg.binding)).intent_id == 'intent1'
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_receipt_proof_and_old_snapshot_cannot_be_called_by_runtime_or_reporting(database):
    import asyncpg
    t = await transport(database)
    try:
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            connection = await asyncpg.connect(t.cfg.dsn.replace('life_pg2_runtime@', role + '@'))
            try:
                for function in ('spot_prepare_receipts_verified', 'spot_state_v14'):
                    with pytest.raises(asyncpg.InsufficientPrivilegeError):
                        await connection.fetchval(f'SELECT life_financial.{function}($1)', t.cfg.account_id)
            finally:
                await connection.close()
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_uid_read_is_cached_only_for_same_credentials_and_explicit_deadline(database):
    t = await transport(database)
    reads = account_http(t)
    try:
        captured = await source(t)
        # Exercise collection without consuming/arming the financial command.
        request = []
        original = t.guard.authorize

        async def hold(value):
            request.append(value)
            raise StorageError('SYNTHETIC_CAPTURE_ONLY')
        t.guard.authorize = hold
        with pytest.raises(StorageError, match='SYNTHETIC_CAPTURE_ONLY'):
            await t.place()
        t.guard.authorize = original
        first = await captured.capture(request[0])
        second = await captured.capture(request[0])
        assert first.binding == second.binding == t.cfg.binding and len(reads) == 1
        captured._uid_at -= timedelta(seconds=2)
        await captured.capture(request[0])
        assert len(reads) == 2
        t.connector.okx_secret_key = 'changed-secret'
        with pytest.raises(StorageError, match='PG_ACCOUNT_CREDENTIAL_CHANGED'):
            await captured.capture(request[0])
        assert len(reads) == 2
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['credential', 'uid_expiry', 'clock'])
async def test_final_local_source_gate_rechecks_after_arm_commit(database, monkeypatch, fault):
    # This scenario targets local revocation after successful physical arm,
    # rather than the separate short-age SQL rejection scenarios.
    t = await transport(database, action_fact_age_ms=5000, admission_age_ms=5000)
    account_http(t)
    captured = await source(t, uid_age_ms=5000, auth_age_ms=5000)
    original = t.repo.arm_quote_action
    reached_commit = []

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        reached_commit.append(result.state)
        if fault == 'credential':
            t.connector.okx_secret_key = 'changed-secret'
        elif fault == 'uid_expiry':
            captured._uid_at -= timedelta(seconds=6)
        else:
            t.journal.clock = lambda: captured._uid_at - timedelta(seconds=1)
        return result
    monkeypatch.setattr(t.repo, 'arm_quote_action', changed)
    try:
        with pytest.raises(PermissionError):
            await t.place(guard=captured.guard(action=t.create, preparation=t.preparation))
        assert reached_commit == ['SEND_UNKNOWN']
        assert not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'SEND_UNKNOWN'
        assert (await t.repo.balance(t.cfg.binding)).reserved_quote_usdt > 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_local_db_clock_skew_denies_source_construction(database):
    t = await transport(database)
    try:
        original = t.journal.clock()
        t.journal.clock = lambda: original + timedelta(seconds=2)
        with pytest.raises(StorageError, match='PG_ACCOUNT_CLOCK_UNQUALIFIED'):
            await source(t)
        assert not t.session.sent
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_source_capacity_and_cancellation_are_bounded_and_never_arm(database):
    t = await transport(database)
    account_http(t)
    captured = await source(t)
    entered, release = asyncio.Event(), asyncio.Event()
    original = t.session.request

    async def held(**kwargs):
        if kwargs['url'].endswith('/account/config'):
            entered.set()
            await release.wait()
        return await original(**kwargs)
    t.session.request = held
    try:
        # Each guard has its own one-shot local capture; only two may collect.
        guards = [captured.guard(action=t.create, preparation=t.preparation) for _ in range(3)]
        first = asyncio.create_task(t.place(guard=guards[0]))
        await asyncio.wait_for(entered.wait(), 2)
        second = asyncio.create_task(t.place(guard=guards[1]))
        await asyncio.sleep(0)
        with pytest.raises(StorageError, match='PG_ACCOUNT_SOURCE_BACKPRESSURE'):
            await t.place(guard=guards[2])
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        assert captured.pending_count == 0 and not t.session.sent
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        release.set()
        await t.close()

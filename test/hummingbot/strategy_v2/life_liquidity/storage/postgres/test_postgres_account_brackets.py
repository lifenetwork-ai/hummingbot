"""Opening/closing signed UID responses also prove the account model."""
import json
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_source import sources
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_observer import observer

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError


@pytest.mark.asyncio
async def test_joint_capture_uses_two_original_config_brackets_instead_of_four_reads(database):
    t = await sources(database, clean=True, fresh=True)
    try:
        report = await t.joint.collect(include_hedge_market=True)
        assert report.account.cash_usdt == 10 and not report.trading_permission
        reads = [r for r in t.requests if r['url'].endswith('/account/config')]
        assert len(reads) == 2
        saved = await t.source.reader._journal.read_collection(t.source.reader.collection_id, max_rows=128, max_bytes=4194304)
        configs = [r for r in saved if r.body['path'] == '/api/v5/account/config']
        assert len(configs) == 2
        assert configs[0] is saved[0] and configs[1] is saved[-1]
        algo = [r for r in t.requests if r['url'].endswith('/orders-algo-pending')]
        assert len(algo) == 7 and {r['params']['ordType'] for r in algo} == set(t.source.reader.SWAP_ALGO_TYPES)
        assert t.requests.index(algo[0]) > next(i for i, r in enumerate(t.requests) if r['url'].endswith('/leverage-info'))
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('autoLoan', True), ('acctLv', '3'), ('posMode', 'net_mode')])
async def test_closing_uid_with_changed_account_model_cannot_publish_capital_or_carry(database, field, value):
    t = await sources(database, clean=True, fresh=True)
    original, changed = t.session.request, []

    async def http(**kwargs):
        response = await original(**kwargs)
        if kwargs['url'].endswith('/account/config') and t.public_received_at is not None:
            body = response.json

            async def malformed():
                result = await body()
                result['data'][0][field] = value
                changed.append(True)
                return result
            response.json = malformed
        return response
    t.session.request = http
    try:
        with pytest.raises(StorageError, match='MODE_UNSUPPORTED|SOURCE_CHANGED'):
            await t.app.observe_sources('changed-close')
        assert changed == [True]
        assert (await t.source.capital.frame(t.cfg.binding))['snapshot'] is None
        assert (await t.source.carry.frame(t.cfg.binding))['bundle'] is None
        saved = await t.source.reader._journal.read_collection(t.source.reader.collection_id, max_rows=128, max_bytes=4194304)
        assert json.loads(saved[-1].body['response_json'])['data'][0][field] == value
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_intake_closing_account_mode_change_cannot_publish_new_money(database):
    t = await sources(database, apply_batch=False)
    intake = observer(t)
    original, changed = t.session.request, []

    async def http(**kwargs):
        response = await original(**kwargs)
        if kwargs['url'].endswith('/account/config') and t.public_received_at is not None:
            body = response.json

            async def malformed():
                result = await body()
                result['data'][0]['autoLoan'] = True
                changed.append(True)
                return result
            response.json = malformed
        return response
    t.session.request = http
    try:
        with pytest.raises(StorageError, match='MODE_UNSUPPORTED|SOURCE_CHANGED'):
            await intake.observe('changed-intake-mode')
        assert changed == [True]
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['copy', 'commit_reply', 'read_bound', 'mutated_bracket'])
async def test_configuration_origin_persistence_and_work_bounds_cannot_publish_a_joint_report(database, monkeypatch, fault):
    t = await sources(database, clean=True, fresh=True)
    reader = t.source.reader
    original_uid, original_commit = reader.source._uid, reader._journal.runtime._commit

    async def uid(**kwargs):
        proof = await original_uid(**kwargs)
        return replace(proof) if kwargs.get('configuration') else proof

    async def lost(transaction):
        await original_commit(transaction)
        raise ConnectionError('synthetic lost original configuration COMMIT reply')
    if fault == 'copy':
        monkeypatch.setattr(reader.source, '_uid', uid)
    elif fault == 'commit_reply':
        monkeypatch.setattr(reader._journal.runtime, '_commit', lost)
    elif fault == 'read_bound':
        reader.max_reads = 1
    else:
        original_configuration = reader.account_configuration

        def substitute(*, closing=False):
            if not closing:
                reader._opening_configuration = replace(reader._opening_configuration)
            return original_configuration(closing=closing)
        monkeypatch.setattr(reader, 'account_configuration', substitute)
    try:
        with pytest.raises((StorageError, CommitUnknown)):
            await t.joint.collect()
        assert t.joint._report is None and not reader.runtime.healthy
        assert (await t.source.capital.frame(t.cfg.binding))['snapshot'] is None
        assert not t.session.sent
        if fault == 'read_bound':
            assert len(t.requests) == 1  # No second HTTP request beyond the bound.
    finally:
        monkeypatch.setattr(reader._journal.runtime, '_commit', original_commit)
        await t.financial.close()

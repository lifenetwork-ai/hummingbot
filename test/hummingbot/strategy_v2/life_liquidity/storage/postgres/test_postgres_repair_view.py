"""Private pre-write reuse cannot become a caller capability or survive a write."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import PgJointRepairRepository

SIGNATURE = 'life_financial.joint_batch_from_repair_view(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'


@pytest.mark.asyncio
async def test_real_prewrite_view_and_signed_approval_are_insufficient_without_private_transaction_stage(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), spot_settlement_stage='receipt')
    financial, cfg, owner = values[:3]
    c = database[0]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)

        async def defer(self, *args, **kwargs):
            return None
        with monkeypatch.context() as patch:
            patch.setattr(PgJointRepairRepository, 'resolve', defer)
            _, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        view = await financial._runtime._admit(lambda conn: conn.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id), safety=True)
        body = repair.replacement.json()
        request = dict(kind='JOINT_BATCH', binding=cfg.binding.json(), owner=owner.json(),
                       version=repair_cut.checkpoint.revision, cut=repair_cut.checkpoint.state_digest, command=body)
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, SIGNATURE)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_JOINT_REPAIR_VIEW_UNQUALIFIED'):
            await c.fetchval('SELECT life_financial.joint_batch_from_repair_view($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb,$8::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()),
                             repair_cut.checkpoint.revision, repair_cut.checkpoint.state_digest, fingerprint(request),
                             json.dumps(body), json.dumps(view))
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.joint_repair_staging WHERE account_id=$1)', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['digest', 'account', 'version', 'unverified', 'body', 'command', 'write', 'stage'])
async def test_repair_view_fault_rolls_back_money_and_preserves_original_pending(database, tmp_path, monkeypatch, fault):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), spot_settlement_stage='receipt')
    financial, cfg = values[:2]
    c = database[0]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', SIGNATURE)
        await c.execute('ALTER FUNCTION ' + SIGNATURE + ' RENAME TO test_original_repair_view')
        mutations = dict(
            digest="p_view:=jsonb_set(p_view,'{digest}',to_jsonb(repeat('0',64)));",
            account="p_view:=jsonb_set(p_view,'{account,account_id}',to_jsonb('00000000-0000-0000-0000-000000000000'::text));",
            version="p_view:=jsonb_set(p_view,'{account,version}',to_jsonb(p_version+1));",
            unverified="p_view:=jsonb_set(p_view,'{unverified}','true'::jsonb);",
            body="p_view:=jsonb_set(p_view,'{balances,LIFE,total}',to_jsonb('999'::text));",
            command="cmd:=jsonb_set(cmd,'{operation_id}',to_jsonb('aliased-replacement'::text));",
            write='UPDATE life_financial.accounts SET version=version+1 WHERE account_id=p_account;',
            stage='DELETE FROM life_financial.joint_repair_staging WHERE account_id=p_account;')
        start = definition.index('AS $function$')
        wrapper = definition[:start] + 'AS $function$ BEGIN ' + mutations[fault] + '''
            RETURN life_financial.test_original_repair_view(p_account,p_binding,p_owner,p_version,p_cut,p_fingerprint,cmd,p_view);
            END; $function$;'''
        await c.execute(wrapper)
        await c.execute('REVOKE ALL ON FUNCTION ' + SIGNATURE + ' FROM PUBLIC')
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_VIEW_UNQUALIFIED|PG_EXPECTED_VERSION_CONFLICT'):
            await apply_recovery(database, batch, command, facts, store)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        for table in ('joint_repair_staging', 'joint_repair_facts'):
            assert not await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.' + table + ' WHERE account_id=$1)', cfg.account_id)
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1 AND operation_id IN ('outer-applied','aliased-replacement'))", cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()

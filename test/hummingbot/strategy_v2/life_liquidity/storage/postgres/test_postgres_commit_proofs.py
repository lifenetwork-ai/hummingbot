"""Fresh post-write COMMIT proofs share only their own statement snapshot."""
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_release import setup

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint

PROOFS = ('carry_verified', 'shared_capital_verified', 'shared_release_verified')


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['none', 'carry', 'capital', 'portfolio', 'release'])
async def test_commit_group_reverifies_after_write_without_reusing_a_previous_verdict(database, fault):
    c = database[0]
    values, _, command, *_ = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
        for name in PROOFS:
            await c.execute('CREATE SEQUENCE life_financial.test_count_' + name + ' MINVALUE 0 START 0')
            signatures = ['life_financial.' + name + '(uuid)']
            if name == 'shared_capital_verified':
                # Both complete original and same-invocation carry-aware paths
                # are one logical capital proof. Keep corruption/freshness and
                # the exactly-one-current-COMMIT-walk assertions unchanged.
                signatures.append('life_financial.shared_capital_after_carry_proof(uuid,boolean)')
            for signature in signatures:
                definition = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
                await c.execute(definition.replace('BEGIN', "BEGIN PERFORM nextval('life_financial.test_count_" + name + "');", 1))
        before = await financial.balance(cfg.binding)
        cut = (await financial.snapshot(cfg.binding)).cut
        version, digest = financial._cut(cut, owner)
        request = dict(kind='SHARED_RELEASE', binding=cfg.binding.json(), owner=owner.json(),
                       version=version, cut=digest, command=command.json())
        transaction = c.transaction()
        await transaction.start()
        try:
            if fault == 'release':
                # Disable before INSERT queues a release-table trigger; account
                # COMMIT guards remain enabled throughout this owner-only fault.
                await c.execute('ALTER TABLE life_financial.shared_claim_releases DISABLE TRIGGER USER')
            result = json.loads(await c.fetchval('''SELECT life_financial.shared_release_command(
                $1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)''', cfg.account_id, json.dumps(cfg.binding.json()),
                                                 json.dumps(owner.json()), version, digest, fingerprint(request), json.dumps(command.json())))
            assert result['released'] and result['trading_permission'] is False
            # The command already checked the old state. Deliberate owner-level
            # damage afterwards must still be found by the real deferred guards.
            corruptions = {
                'carry': ('carry_checks', "bundle=bundle||'{\"unexpected\":true}'::jsonb"),
                'capital': ('shared_capital_observations', "source_hash=repeat('0',64)"),
                'portfolio': ('portfolio_observations', "measurement=measurement||'{\"unexpected\":true}'::jsonb"),
                'release': ('shared_claim_releases', "basis=basis||'{\"unexpected\":true}'::jsonb"),
            }
            if fault != 'none':
                table, change = corruptions[fault]
                if fault != 'release':
                    await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
                await c.execute('UPDATE life_financial.' + table + ' SET ' + change + ' WHERE account_id=$1', cfg.account_id)
                if fault != 'release':
                    await c.execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
                # Release INSERT also queues internal FK events. Its trigger
                # enable state is restored by the enclosing rollback instead.
            for name in PROOFS:
                await c.fetchval("SELECT setval('life_financial.test_count_" + name + "',0,false)")
            if fault == 'none':
                await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
                counts = {name: await c.fetchval('SELECT CASE WHEN is_called THEN last_value+1 ELSE 0 END FROM life_financial.test_count_' + name)
                          for name in PROOFS}
                assert counts == dict.fromkeys(PROOFS, 1)
            else:
                with pytest.raises(asyncpg.RaiseError, match='LIFE_.*UNVERIFIED'):
                    await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        finally:
            await transaction.rollback()
        assert await financial.balance(cfg.binding) == before
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases') == 0
    finally:
        await financial.close()

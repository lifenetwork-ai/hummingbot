"""Completed real File repair batches remain original audit history at retirement."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch


async def prior_repair(database, monkeypatch, state, *, superseded):
    async def before_joint(store, financial, native_owner, file_owner, joint):
        original = joint.spot_fills[0]
        config = dict(binding=store.binding, limits=store.limits, risk_limits=store._risk_limits,
                      subsidy_policy=store._subsidy_policy, loss_policy=store._loss_policy,
                      config_version=1, account_lock_root=state['path'] / 'locks', clock=store._clock,
                      fill_policy=store._fill_policy, accounting_policy=store._accounting_policy,
                      recovery_policy=store._recovery_policy, operator_keys=store._operator_keys)
        files = store._files

        async def cold_open(current):
            await current.close()
            return await open_legacy_spot(files, **config)

        publish = store._publish_fills

        def interrupted_fill(key, *args, **kwargs):
            if key.startswith('fill-done:'):
                raise OSError('synthetic original fill receipt interruption')
            return publish(key, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(store, '_publish_fills', interrupted_fill)
            with pytest.raises(CommitUnknown):
                await store.apply_fills(original, cut=(await store.snapshot(store.binding)).cut, owner=file_owner)
        store = await cold_open(store)
        evidence = SpotFinancialRecoveryFacts(store.binding, joint.observed_at, joint.account_base_life,
                                              joint.account_quote_usdt + D('.1'), D('0'), D('0'),
                                              tuple(f.trade_id for f in original.fills), (), (original.client_order_id,), (), True, True, True)
        predecessors = []
        for name in ('original-first-repair', 'original-second-repair')[:int(superseded)]:
            first = SpotRecoveryBatch(name, (original,), (), (), tuple(predecessors))
            publish = store._publish_recovery_batch

            def interrupted_batch(key, *args, **kwargs):
                if key.startswith('batch-done:'):
                    raise OSError('synthetic original repair receipt interruption')
                return publish(key, *args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(store, '_publish_recovery_batch', interrupted_batch)
                with pytest.raises(CommitUnknown):
                    await store.recover_spot_batch(first, facts=evidence, acknowledgements=(),
                                                   cut=(await store.snapshot(store.binding)).cut, owner=file_owner)
            store = await cold_open(store)
            predecessors.append(name)
        repaired = SpotRecoveryBatch('original-complete-repair', (original,), (), (), tuple(predecessors))
        assert (await store.recover_spot_batch(repaired, facts=evidence, acknowledgements=(),
                                               cut=(await store.snapshot(store.binding)).cut, owner=file_owner)).state == 'APPLIED'
        # Completing an accounting repair does not re-enable this cold
        # writer. A separate original signed observation-only transition is
        # required before its next core cash command; no activation occurs.
        recovery = OwnerTransition('original-observation-recover', OwnerMode.RECOVERY_ONLY, file_owner)
        cut = (await store.snapshot(store.binding)).cut
        signed = KEYS.sign('operator', store.binding, 'RECOVER', recovery.json(), cut, evidence,
                           issued_at=evidence.observed_at, expires_at=evidence.observed_at + timedelta(seconds=10))
        await store.transition_owner(recovery, approval=signed, facts=evidence, cut=cut)
        assert (await financial.apply_received(original, owner=native_owner)).state == 'APPLIED'
        state['native_operation'] = original.operation_id
        state['original_receipt'] = await database[0].fetchval('SELECT to_jsonb(o) FROM life_financial.operations o WHERE operation_id=$1', original.operation_id)
        # The first response for the NEW funding bill is constructed by setup
        # only after this completed recovery and independent native receipt.
        return store, await database[0].fetchval('SELECT clock_timestamp()')
    return before_joint


@pytest.mark.asyncio
@pytest.mark.parametrize('side,currency', [('BUY', 'USDT'), ('SELL', 'LIFE')])
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
@pytest.mark.parametrize('superseded', [False, True, 2])
async def test_original_completed_file_recovery_batches_compose_with_later_funding_without_rebooking(database, tmp_path, monkeypatch, side, currency, stage, superseded):
    state, expected = {'path': tmp_path}, {}
    hook = await prior_repair(database, monkeypatch, state, superseded=superseded)
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, spot_fill=(side, currency), before_joint=hook, file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, database[0])
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, database[0])
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        assert await database[0].fetchval('SELECT to_jsonb(o) FROM life_financial.operations o WHERE operation_id=$1', state['native_operation']) == state['original_receipt']
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()

"""An actual File SPOT repair remains original context in later outer recovery."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch


async def repair_original_spot(database, path, monkeypatch, joint, store, recovery, values, file_owner,
                               financial, native_owner, prepared, *, currency, book_native):
    """Repair SPOT BEFORE observing the later SWAP trade and funding bill."""
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
    at = recovery.clock_ms()
    leaf = SpotFillObservation('spottrade1', D('1'), D('1'), currency, D('-.02'), at,
                               D('1'), at, at, 'independent_market')
    fill = FillSpot('original-spot-fill', prepared.operation_id, prepared.intent_id, prepared.client_order_id,
                    prepared.market, prepared.side, prepared.session_id, prepared.epoch, '123456789', D('1'), (leaf,), values[6].wall)
    base = D('10') + (D('1') if prepared.side == 'BUY' else D('-1')) - (D('.02') if currency == 'LIFE' else D('0'))
    cash = D('10') + (D('-1') if prepared.side == 'BUY' else D('1')) - (D('.02') if currency == 'USDT' else D('0'))
    facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, base, cash, D('0'), D('0'),
                                       ('spottrade1',), (), (prepared.client_order_id,), (), True, True, True)
    publish = store._publish_fills

    def lose_receipt(key, *args, **kwargs):
        if key.startswith('fill-done:'):
            raise OSError('synthetic original File fill receipt loss before SWAP trade')
        return publish(key, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store, '_publish_fills', lose_receipt)
        with pytest.raises(CommitUnknown):
            await store.apply_fills(fill, cut=(await store.snapshot(store.binding)).cut, owner=file_owner)
    joint, store, recovery, values = await reopen(path, store, recovery, values, file_owner)
    repaired = SpotRecoveryBatch('original-outer-spot-repair', (fill,), (), ())
    assert (await store.recover_spot_batch(repaired, facts=facts, acknowledgements=(),
                                           cut=(await store.snapshot(store.binding)).cut, owner=file_owner)).state == 'APPLIED'
    observe = OwnerTransition('original-outer-observation-recover', OwnerMode.RECOVERY_ONLY, file_owner)
    cut = (await store.snapshot(store.binding)).cut
    signed = KEYS.sign('operator', store.binding, 'RECOVER', observe.json(), cut, facts,
                       issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=10))
    await store.transition_owner(observe, approval=signed, facts=facts, cut=cut)
    if book_native:
        assert (await financial.apply_received(fill, owner=native_owner)).state == 'APPLIED'
    return joint, store, recovery, values, fill


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'receipt', 'cash_done'])
@pytest.mark.parametrize('currency', ['USDT', 'LIFE'])
@pytest.mark.parametrize('booked', [False, True])
async def test_original_file_spot_repair_and_completed_outer_parent_compose_with_later_funding(database, tmp_path, monkeypatch, stage, currency, booked):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, funding=D('-.1'), spot_side='BUY', spot_fee_currency=currency,
        file_result=expected, prior_outer=True, prior_file_repair=True, native_prior_file_repair=booked)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        original = await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-fill'", cfg.account_id)
        assert bool(original) == booked
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
        if booked:
            assert await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-fill'", cfg.account_id) == original
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()

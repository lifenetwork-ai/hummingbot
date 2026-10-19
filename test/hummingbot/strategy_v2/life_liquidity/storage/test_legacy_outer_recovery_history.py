"""An original File repair remains audit context in later outer accounting."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryPayment, RecoveryTrade
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch


@pytest.mark.asyncio
async def test_original_completed_spot_repair_is_retained_during_later_outer_cash_recovery(tmp_path, monkeypatch):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, spot=True, operator_keys=KEYS)
    try:
        assert await repo.reconcile(store.binding, owner)
        spot = replace(order(owner, fee_buffer_quote_usdt=D('.1')), risk_epoch=1)
        spotclaim = CapitalClaim(spot.intent_id, spot.client_order_id, store.binding.account_uid, str(owner.session_id), owner.epoch, 1, 1, 'SPOT', 'BUY', 'OPEN', D('2'), D('1'), 1)
        assert (await store._call(values[1].reserve, spotclaim)).allowed
        await store.prepare(spot, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        await store.arm(spot.operation_id, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        claim = await store._call(send_close, values)
        now = values[6].wall
        at = recovery.clock_ms()
        leaf = SpotFillObservation('spot-trade', D('1'), D('1'), 'USDT', D('-.02'), at, D('1'), at, at, 'independent_market')
        fill = FillSpot('old-spot-fill', spot.operation_id, spot.intent_id, spot.client_order_id, spot.market, spot.side, spot.session_id, spot.epoch, '123456789', D('1'), (leaf,), now)
        facts = SpotFinancialRecoveryFacts(store.binding, now, D('11'), D('8.98'), D(0), D(0), ('spot-trade',), (), (spot.client_order_id,), (), True, True, True)
        publish = store._publish_fills

        def stop(key, *args, **kwargs):
            if key.startswith('fill-done:'):
                raise OSError('synthetic original fill receipt loss')
            return publish(key, *args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(store, '_publish_fills', stop)
            with pytest.raises(CommitUnknown):
                await store.apply_fills(fill, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        repair = SpotRecoveryBatch('original-complete-repair', (fill,), (), ())
        assert (await store.recover_spot_batch(repair, facts=facts, acknowledgements=(), cut=(await store.snapshot(store.binding)).cut, owner=owner)).state == 'APPLIED'
        cmd = OwnerTransition('observation-recover', OwnerMode.RECOVERY_ONLY, owner)
        cut = (await store.snapshot(store.binding)).cut
        signed = KEYS.sign('offline-operator', store.binding, 'RECOVER', cmd.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        await store.transition_owner(cmd, approval=signed, cut=cut, facts=facts)
        values[6].advance(0.1)
        original = report(values, bundle, claim)
        stamp = recovery.clock_ms()
        bundle['value'] = replace(bundle['value'], observed_at_ms=stamp)
        values[7]['value'] = replace(values[7]['value'], observed_at_ms=stamp)
        values[8]['snapshot'] = replace(values[8]['snapshot'], observed_at_ms=stamp)
        bundle['value'] = replace(bundle['value'], orders=(replace(original, exchange_order_id='987654321'), RecoveryOrder(spotclaim, '123456789', 'partially_filled', (RecoveryTrade('spot-trade', D('1'), D('1'), 'USDT', D('-.02'), D(0), at),))), pending_wire_ids=(spot.client_order_id,))
        values[8]['snapshot'] = replace(values[8]['snapshot'], spot_life_base=D('11'), spot_cash_quote=D('8.979'), pending=(spotclaim,))
        facts = replace(facts, observed_at=values[6].wall, account_quote_usdt=D('8.979'))
        assert await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts), recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        values[6].advance(0.1)
        stamp = recovery.clock_ms()
        seq = values[8]['snapshot'].sequence + 1
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=seq, observed_at_ms=stamp, spot_cash_quote=D('8.879'))
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=seq, observed_at_ms=stamp)
        bundle['value'] = replace(bundle['value'], snapshot_sequence=seq, observed_at_ms=stamp, payments=(RecoveryPayment('101', D('-.1'), stamp),))
        facts = replace(facts, observed_at=values[6].wall, account_quote_usdt=D('8.879'))
        apply = store._ledger.apply_joint_snapshot

        def stopcash(*args, **kwargs):
            apply(*args, **kwargs)
            raise OSError('synthetic later outer cash interruption')
        with monkeypatch.context() as patch:
            patch.setattr(store._ledger, 'apply_joint_snapshot', stopcash)
            assert not await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts)
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        facts = replace(facts, account_trade_ids=('trade-1', 'spot-trade'), accepted_wire_ids=(claim.wire_id, spot.client_order_id))
        cut = (await store.snapshot(store.binding)).cut
        cmd = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        signed = KEYS.sign('offline-operator', store.binding, 'DRAIN', cmd.json(), cut, facts, issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=10))
        await store.transition_owner(cmd, approval=signed, cut=cut, facts=facts)
        source = await store.legacy_applying_source(store.binding, facts=facts, cut=(await store.snapshot(store.binding)).cut, joint=repo)
        trade = original.trades[0]
        swapleaf = SwapFillObservation(trade.trade_id, trade.quantity_base, trade.price_usdt, trade.fee_currency, trade.signed_fee, trade.at_ms, D(1), trade.at_ms, trade.at_ms, 'independent_market', trade.realized_pnl_quote, trade.sequence)
        swap = FillSwap('target-swap', 'native-original', claim.intent_id, claim.wire_id, 'LIFE-USDT', claim.side, claim.session_id, claim.epoch, '987654321', trade.quantity_base, (swapleaf,), facts.observed_at)
        payment = SimpleNamespace(bill_id='101', settled_at_ms=stamp, signed_payment_quote=D('-.1'))
        batch = SimpleNamespace(spot_fills=(fill,), swap_fills=(swap,), cashflows=(), funding=SimpleNamespace(bundle=SimpleNamespace(funding=SimpleNamespace(payments=(payment,)))))
        before, raw = await store.snapshot(store.binding), await store._call(store._raw)
        basis = joint_profile(source, SimpleNamespace(batch=batch))
        assert basis['spot_recovery_history'][0]['operation_id'] == repair.operation_id
        assert D(basis['opening_quote_usdt']) == D('10')
        assert len(basis['joint_core_history']) == 1
        assert await store.snapshot(store.binding) == before
        assert await store._call(store._raw) == raw
    finally:
        await store.close()

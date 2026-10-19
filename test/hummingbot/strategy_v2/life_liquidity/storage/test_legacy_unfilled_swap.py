"""An unfilled original SWAP retains its claim during funding recovery."""
import json
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import send_close
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryPayment
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_before', 'wallet_after', 'receipt'])
async def test_unfilled_original_swap_has_no_economic_leaf_or_implied_native_preparation(tmp_path, monkeypatch, stage):
    repo, store, recovery, values, box, owner = await fixture(tmp_path, operator_keys=KEYS)
    try:
        assert await repo.reconcile(store.binding, owner)
        claim = await store._call(send_close, values)
        at = recovery.clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, spot_cash_quote=D('9.9'), pending=(claim,))
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=2)
        box['value'] = replace(box['value'], snapshot_sequence=2,
                               orders=(RecoveryOrder(claim, '987654321', 'live', ()),),
                               payments=(RecoveryPayment('101', D('-.1'), at),), pending_wire_ids=(claim.wire_id,))
        target, method = ((repo, 'apply_cash_settlement') if stage == 'outer_before_cash' else
                          (store, '_publish_joint_cash') if stage == 'receipt' else (store._ledger, 'apply_joint_snapshot'))
        original = getattr(target, method)
        fired = []

        def stop(*args, **kwargs):
            if stage == 'receipt' and not args[0].startswith('joint-done:'):
                return original(*args, **kwargs)
            fired.append(True)
            if stage == 'wallet_after':
                original(*args, **kwargs)
            raise OSError('synthetic unfilled SWAP funding interruption')
        with monkeypatch.context() as patch:
            patch.setattr(target, method, stop)
            assert not await repo.reconcile(store.binding, owner)
        assert fired, recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), D('9.9'), D('0'), D('0'),
                                           (), (), (claim.wire_id,), (), True, True, True)
        cut = (await store.snapshot(store.binding)).cut
        drain = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        signed = KEYS.sign('offline-operator', store.binding, 'DRAIN', drain.json(), cut, facts,
                           issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=1))
        await store.transition_owner(drain, approval=signed, cut=cut, facts=facts)
        source = await store.legacy_applying_source(store.binding, facts=facts, cut=(await store.snapshot(store.binding)).cut, joint=repo)
        payment = SimpleNamespace(bill_id='101', settled_at_ms=at, signed_payment_quote=D('-.1'))
        batch = SimpleNamespace(spot_fills=(), swap_fills=(), cashflows=(),
                                funding=SimpleNamespace(bundle=SimpleNamespace(funding=SimpleNamespace(payments=(payment,)))))
        before, raw = await store.snapshot(store.binding), await store._call(store._raw)
        basis = joint_profile(source, SimpleNamespace(batch=batch))
        assert basis['claims'] == [{'claim': json.loads(json.dumps(asdict(claim), default=str)), 'exchange_order_id': '987654321', 'unfilled': True}]
        assert D(basis['opening_quote_usdt']) == D('10')
        assert await store.snapshot(store.binding) == before
        assert await store._call(store._raw) == raw
    finally:
        await store.close()

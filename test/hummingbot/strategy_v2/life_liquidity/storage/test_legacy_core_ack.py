"""Actual original core ACK/funding crash images, without a database or send."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts, open_it
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_settlement import command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_legacy_applying import drain
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import prepare_arm
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import create_fixture
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt_before'])
async def test_original_core_ack_is_read_only_context_for_funding_profile(tmp_path, monkeypatch, stage):
    files, binding, owner = create_fixture(tmp_path)
    store = await open_it(files, binding, tmp_path, owner, True)
    try:
        await prepare_arm(store, binding, owner, acknowledge=False)
        acknowledgement = ack(owner)
        await store.acknowledge(acknowledgement, cut=(await store.snapshot(binding)).cut, owner=owner)
        old = command(binding, owner)
        event = JointCashEvent(old.events[0].event_id, D('-.1'))
        original = replace(old, events=(event,), account_quote_usdt=D('9.9'), spot_acks=(acknowledgement,))
        cut = (await store.snapshot(binding)).cut
        target, method = ((store, '_publish_joint_cash') if stage.startswith('receipt') else
                          (store._ledger, 'apply_joint_snapshot'))
        publish = getattr(target, method)

        def interrupt(*args, **kwargs):
            if stage.startswith('receipt') and not args[0].startswith('joint-done:'):
                return publish(*args, **kwargs)
            if stage.endswith('after'):
                publish(*args, **kwargs)
            raise OSError('synthetic original core ACK interruption')

        with monkeypatch.context() as patch:
            patch.setattr(target, method, interrupt)
            with pytest.raises(CommitUnknown):
                await store.apply_joint_cash(original, cut=cut, owner=owner)
        await store.close()
        store = await open_it(files, binding, tmp_path)
        await drain(store, binding, owner)
        before = await store.snapshot(binding)
        evidence = replace(facts(binding), account_quote_usdt=D('9.9'), accepted_wire_ids=(acknowledgement.client_order_id,))
        source = await store.legacy_applying_source(binding, facts=evidence, cut=before.cut)
        payment = SimpleNamespace(bill_id='101', settled_at_ms=event.at_ms, signed_payment_quote=event.signed_amount_usdt)
        batch = SimpleNamespace(spot_fills=(), swap_fills=(), cashflows=(),
                                funding=SimpleNamespace(bundle=SimpleNamespace(funding=SimpleNamespace(payments=(payment,)))))
        result = funding_profile(source, SimpleNamespace(batch=batch))
        assert result['spot_acknowledgements'] == [acknowledgement.json()]
        assert result['spot_required_ack_ids'] == [acknowledgement.operation_id]
        assert await store.snapshot(binding) == before
        assert source == await store.legacy_applying_source(binding, facts=evidence, cut=before.cut)
    finally:
        await store.close()

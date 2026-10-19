"""Real earlier APPLIED joint cash is history, never an opening-wallet seed."""
import hashlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import KEYS
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture, reopen
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@asynccontextmanager
async def captured_history(tmp_path, monkeypatch, stage, *, earlier_funding=False):
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path, operator_keys=KEYS)
    try:
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        claim = await store._call(send_close, values)
        original = report(values, bundle, claim)
        bundle['value'] = replace(bundle['value'], orders=(replace(original, exchange_order_id='123456789'),))
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        payments = []

        def publish(bill, amount):
            values[6].advance(.1)
            at = recovery.clock_ms()
            sequence = values[8]['snapshot'].sequence + 1
            payments.append(RecoveryPayment(bill, amount, at))
            values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=sequence, observed_at_ms=at,
                                            spot_cash_quote=values[8]['snapshot'].spot_cash_quote + amount)
            values[7]['value'] = replace(values[7]['value'], snapshot_sequence=sequence, observed_at_ms=at)
            bundle['value'] = replace(bundle['value'], snapshot_sequence=sequence, observed_at_ms=at, payments=tuple(payments))
        if earlier_funding:
            publish('100', D('.02'))
            assert await repo.reconcile(store.binding, owner), recovery.reason_code
            repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        publish('101', D('-.1'))
        target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet') else
                          (recovery, '_apply') if stage == 'cash_done' else (repo, 'apply_cash_settlement'))
        apply = getattr(target, method)
        fired = []

        def stop(*args, **kwargs):
            fired.append(True)
            if stage == 'wallet_after':
                apply(*args, **kwargs)
            raise OSError('synthetic second original outer core interruption')
        with monkeypatch.context() as patch:
            patch.setattr(target, method, stop)
            assert not await repo.reconcile(store.binding, owner)
        assert fired, recovery.reason_code
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        facts = SpotFinancialRecoveryFacts(store.binding, values[6].wall, D('10'), values[8]['snapshot'].spot_cash_quote, D(0), D(0), ('trade-1',), (), (claim.wire_id,), (), True, True, True)
        cut = (await store.snapshot(store.binding)).cut
        drain = OwnerTransition('drain', OwnerMode.DRAINING, owner)
        sig = KEYS.sign('offline-operator', store.binding, 'DRAIN', drain.json(), cut, facts, issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=1))
        await store.transition_owner(drain, approval=sig, facts=facts, cut=cut)
        source = await store.legacy_applying_source(store.binding, facts=facts, cut=(await store.snapshot(store.binding)).cut, joint=repo)
        assert len(source.original_operations) == (1 if stage == 'outer_before_cash' else 2) + int(earlier_funding)
        t = original.trades[0]
        leaf = SwapFillObservation(t.trade_id, t.quantity_base, t.price_usdt, t.fee_currency, t.signed_fee, t.at_ms, D('1'), t.at_ms, t.at_ms, 'independent_market', t.realized_pnl_quote, t.sequence)
        target = FillSwap('target-swap', 'native-original-prepare', claim.intent_id, claim.wire_id, 'LIFE-USDT', claim.side, claim.session_id, claim.epoch, '123456789', t.quantity_base, (leaf,), facts.observed_at)
        observed = tuple(SimpleNamespace(bill_id=p.bill_id, settled_at_ms=p.at_ms, signed_payment_quote=p.signed_amount_quote) for p in payments)
        batch = SimpleNamespace(spot_fills=(), swap_fills=(target,), cashflows=(), funding=SimpleNamespace(bundle=SimpleNamespace(funding=SimpleNamespace(payments=observed))))
        yield store, source, batch
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_before', 'wallet_after', 'cash_done'])
@pytest.mark.parametrize('earlier_funding', [False, True])
async def test_prior_completed_outer_core_is_not_a_new_wallet_anchor(tmp_path, monkeypatch, stage, earlier_funding):
    async with captured_history(tmp_path, monkeypatch, stage, earlier_funding=earlier_funding) as (store, source, batch):
        before, raw = await store.snapshot(store.binding), await store._call(store._raw)
        basis = joint_profile(source, SimpleNamespace(batch=batch))
        assert D(basis['opening_quote_usdt']) == D('10')
        assert len(basis['joint_core_history']) == 1 + int(earlier_funding)
        assert await store.snapshot(store.binding) == before
        assert await store._call(store._raw) == raw


@pytest.mark.asyncio
async def test_history_rejects_missing_money_origin_or_changed_original_events_even_with_rehashed_before_images(tmp_path, monkeypatch):
    async with captured_history(tmp_path, monkeypatch, 'wallet_after', earlier_funding=True) as (store, source, batch):
        before, original = await store.snapshot(store.binding), await store._call(store._raw)
        for fault in ('pending', 'wallet_seed', 'fee_split', 'future', 'binding', 'publisher', 'writer', 'revision'):
            checkpoint = json.loads(source.checkpoint_bytes)
            resources = checkpoint['state']['resources']
            rows = resources['joint_cash_operations']
            key = next(key for key in rows if key != source.pending_operation_ids[0])
            row = rows[key]
            if fault == 'pending':
                row['state'] = 'APPLYING'
            elif fault == 'wallet_seed':
                wallet = json.loads(bytes.fromhex(row['before_native']['reservations']))
                wallet['usdt_balance'] = '11'
                row['before_native']['reservations'] = encode(wallet).hex()
                row['cut']['components'] = [[name, hashlib.sha256(bytes.fromhex(data)).hexdigest()] for name, data in sorted(row['before_native'].items())]
            elif fault == 'fee_split':
                row['command']['events'][0].update(signed_fee_usdt='0', realized_pnl_usdt='-0.001')
            elif fault == 'future':
                row['command']['observed_at'] = (source.facts.observed_at + timedelta(seconds=1)).isoformat()
            elif fault == 'binding':
                row['command']['binding']['account_uid'] = '99999'
            elif fault == 'publisher':
                for value in resources['joint_cashflows'].values():
                    value['operation_id'] = source.pending_operation_ids[0]
            elif fault == 'writer':
                row['owner']['process_id'] = '00000000-0000-0000-0000-000000000999'
            else:
                row['cut']['checkpoint']['revision'] = rows[source.pending_operation_ids[0]]['cut']['checkpoint']['revision']
            row['fingerprint'] = fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']})
            # Only the pure admission parser receives these diagnostic copies.
            # They are not signed capsules and never reach a native writer.
            changed = SimpleNamespace(cut=source.cut, facts=source.facts, checkpoint_bytes=encode(checkpoint),
                                      ancillary_images=source.ancillary_images, journal_images=source.journal_images,
                                      original_operations=rows, pending_operation_ids=source.pending_operation_ids)
            with pytest.raises(StorageError):
                joint_profile(changed, SimpleNamespace(batch=batch))
        assert await store.snapshot(store.binding) == before
        assert await store._call(store._raw) == original

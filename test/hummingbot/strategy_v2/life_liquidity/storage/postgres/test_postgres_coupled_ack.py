"""Original ACK interruptions inside an outer SPOT/SWAP monetary recovery."""
import copy
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import (
    setup as core_setup,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'cash_done'])
@pytest.mark.parametrize('part,written', [('wal', False), ('wal', True), ('receipt', False), ('receipt', True)])
async def test_original_ack_interruption_keeps_exact_native_receipt_and_file_money(database, tmp_path, monkeypatch, side, stage, part, written):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, spot_side=side, funding=D('-.1'), file_result=expected, spot_ack=(part, written))
    financial, cfg, owner = values[:3]
    cold = None
    try:
        original = await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-ack'", cfg.account_id)
        assert original
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
        attempt = await cold.attempt('spotintent', cfg.binding)
        assert attempt.exchange_order_id == '123456789'
        assert attempt.state.value != 'TERMINAL'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-ack'", cfg.account_id) == original
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
async def test_original_ack_bearing_core_funding_retains_native_identity_across_cold_repair(database, tmp_path, monkeypatch, side, stage):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await core_setup(
        database, tmp_path, monkeypatch, stage, spot_fill=(side, 'USDT'), spot_open_only=True, core_ack=True, file_result=expected)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        original = await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='ack1'", cfg.account_id)
        assert original
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
        assert (await cold.attempt('intent1', cfg.binding)).exchange_order_id == '123456789'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await database[0].fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='ack1'", cfg.account_id) == original
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
async def test_core_ack_without_native_original_cannot_be_inferred_from_funding_cash(database, tmp_path, monkeypatch):
    values, _, command, cut, facts, approval, importer, store, source = await core_setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), spot_open_only=True, core_ack=True, native_core_ack=False)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('part,written', [('wal', False), ('wal', True), ('receipt', False), ('receipt', True)])
async def test_missing_independent_native_original_ack_cannot_be_adopted(database, tmp_path, monkeypatch, part, written):
    values, _, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), spot_ack=(part, written), native_ack=False)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        if part == 'receipt' and written:
            # A complete File ACK can remain diagnostic without an original
            # native ACK, preserving the earlier import contract. Import must
            # never infer the native ACK or apply the target money.
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
            assert (await financial.attempt('spotintent', cfg.binding)).state.value == 'SEND_UNKNOWN'
        else:
            with pytest.raises(StorageError):
                await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        if not (part == 'receipt' and written):
            assert after.cut == before.cut
        assert await values[5].claims(cfg.binding) == claims
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
async def test_required_native_ack_proof_keeps_original_values_receipt_event_and_historical_cut(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_joint import joint_profile
    values, _, command, cut, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), spot_ack=('wal', False))
    financial, cfg = values[:2]
    c = database[0]
    basis = joint_profile(source, command)
    operation = basis['spot_required_ack_ids'][0]
    try:
        async def valid(value, version=cut.checkpoint.revision):
            return await c.fetchval('SELECT life_financial.legacy_required_ack_prefix_valid($1,$2::jsonb,$3)', cfg.account_id, json.dumps(value), version)
        assert await valid(basis)
        for fault in ('missing', 'duplicate', 'kind', 'time', 'wire', 'exchange', 'projection_missing', 'projection_duplicate'):
            changed = copy.deepcopy(basis)
            if fault == 'missing':
                changed['spot_required_ack_ids'] = ['missing-original']
            elif fault == 'duplicate':
                changed['spot_required_ack_ids'] *= 2
            elif fault == 'kind':
                changed['spot_required_ack_ids'] = ['spot-prepare']
            elif fault == 'projection_missing':
                changed['spot_acknowledgements'] = []
            elif fault == 'projection_duplicate':
                changed['spot_acknowledgements'] *= 2
            else:
                key, value = {'time': ('observed_at', '2000-01-01T00:00:00+00:00'),
                              'wire': ('client_order_id', 'foreign'), 'exchange': ('exchange_order_id', 'foreign')}[fault]
                changed['spot_acknowledgements'][0][key] = value
            assert not await valid(changed), fault
        assert not await valid(basis, -1)
        for table, sql in (
            ('operations', "UPDATE life_financial.operations SET fingerprint=repeat('f',64) WHERE account_id=$1 AND operation_id=$2"),
            ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{version}','0') WHERE account_id=$1 AND operation_id=$2"),
            ('financial_events', "UPDATE life_financial.financial_events SET source_hash=repeat('f',64) WHERE account_id=$1 AND event_id='ack:spotwire' AND $2::text IS NOT NULL"),
        ):
            tx = c.transaction()
            await tx.start()
            try:
                await c.execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
                result = await c.execute(sql, cfg.account_id, operation)
                assert result == 'UPDATE 1'
                assert not await valid(basis)
            finally:
                await tx.rollback()
        assert await valid(basis)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()

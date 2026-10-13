"""Synthetic product ports use actual shared-capital/hedge/carry journals."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY, create_fixture
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital, open_file_products


async def fixture(tmp_path):
    _, binding, owner = create_fixture(tmp_path / 'spot')
    capital, feed = setup(tmp_path)
    clock = {'now': owner.valid_from}
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: clock['now'])
    repo = await open_file_products(store, capital=capital)
    return repo, store, owner, feed, clock


def bound_claim(owner, **kwargs):
    return replace(claim(**kwargs), session_id=str(owner.session_id), epoch=owner.epoch)


@pytest.mark.asyncio
async def test_capital_ports_keep_shared_margin_units_and_permanent_operation_identity(tmp_path):
    repo, store, owner, _, _ = await fixture(tmp_path)
    try:
        cut = await repo.cut(store.binding)
        command = ReserveCapital('reserve-1', bound_claim(owner))
        receipt = await repo.reserve(command, cut, owner)
        assert receipt.decision.allowed
        assert receipt.decision.spot_buy_hold_quote == Decimal('6')
        assert (await repo.reserve(command, cut, owner)).replayed
        with pytest.raises(StorageError, match='PRODUCT_OPERATION_CONFLICT'):
            await repo.reserve(replace(command, claim=replace(command.claim, quantity_base=Decimal('7'))), cut, owner)
        next_cut = await repo.cut(store.binding)
        denied = await repo.reserve(ReserveCapital('reserve-2', bound_claim(owner, key='swap', product='SWAP',
                                                                            side='SELL')), next_cut, owner)
        assert not denied.decision.allowed
        assert denied.decision.reason_code == 'CAPITAL_INITIAL_MARGIN_LOW'
        assert (await repo.claims(store.binding))[0] == command.claim
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_native_cut_changes_and_unknown_external_claims_cannot_be_adopted(tmp_path):
    repo, store, owner, _, _ = await fixture(tmp_path)
    try:
        cut = await repo.cut(store.binding)
        assert repo._capital.reserve(bound_claim(owner, key='foreign', amount='1')).allowed
        with pytest.raises(StorageError, match='PRODUCT_CUT_STALE'):
            await repo.reserve(ReserveCapital('stale', bound_claim(owner, amount='1')), cut, owner)
        fresh = await repo.cut(store.binding)
        with pytest.raises(StorageError, match='PRODUCT_NATIVE_IMPORT_REQUIRED'):
            await repo.reserve(ReserveCapital('adopt', bound_claim(owner, key='foreign', amount='1')), fresh, owner)
        assert (await repo.claims(store.binding))[0].intent_id == 'foreign'
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_restore_reads_receipt_without_rearming_or_mutating_native_claims(tmp_path):
    repo, store, owner, _, clock = await fixture(tmp_path)
    cut = await repo.cut(store.binding)
    command = ReserveCapital('known', bound_claim(owner, amount='1'))
    await repo.reserve(command, cut, owner)
    await store.close()
    restored = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=store.binding,
                                limits=CAPACITY, create=False, clock=lambda: clock['now'])
    native, _ = setup(tmp_path, create=False)
    try:
        cold = await open_file_products(restored, capital=native)
        clock['now'] = owner.expires_at + timedelta(seconds=1)
        assert (await cold.reserve(command, cut, owner)).replayed  # History, not current authority.
        with pytest.raises(StorageError, match='PRODUCT_OWNER_REVOKED_OR_EXPIRED'):
            await cold.reserve(ReserveCapital('new', bound_claim(owner, key='new', amount='1')),
                               await cold.cut(store.binding), owner)
        assert len(await cold.claims(store.binding)) == 1
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_policy_mismatch_cannot_alias_the_same_native_file(tmp_path):
    repo, store, _, _, _ = await fixture(tmp_path)
    try:
        repo._capital.policy = replace(repo._capital.policy, account_uid='999')
        # PolicyState keeps its independently bound policy; both projections must agree.
        with pytest.raises(StorageError, match='PRODUCT_POLICY_MISMATCH'):
            await repo.check_capital(store.binding)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_native_noop_has_a_durable_barrier_and_cannot_publish_permission(tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalDecision
    repo, store, owner, _, _ = await fixture(tmp_path)
    monkeypatch.setattr(repo._capital, 'reserve', lambda claim: CapitalDecision(True, 'CAPITAL_READY'))
    try:
        cut = await repo.cut(store.binding)
        with pytest.raises(StorageError, match='PRODUCT_NATIVE_POSTCONDITION_FAILED'):
            await repo.reserve(ReserveCapital('noop', bound_claim(owner)), cut, owner)
        assert store._uncertain
    finally:
        await store.close()
    cold = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=store.binding,
                            limits=CAPACITY, create=False, clock=lambda: owner.valid_from)
    try:
        native, _ = setup(tmp_path, create=False)
        restored = await open_file_products(cold, capital=native)
        row = (await cold.read(store.binding)).resources['product_operations']['noop']
        assert row['state'] == 'APPLYING'
        with pytest.raises(StorageError, match='PRODUCT_OPERATION_REQUIRES_RECOVERY'):
            await restored.reserve(ReserveCapital('noop', bound_claim(owner)), cut, owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_actual_carry_port_keeps_funding_liability_and_signed_settlements(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_carry_monitor import refresh, setup_carry

    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    monitor, values, feed = setup_carry(tmp_path / 'native')
    _, binding, owner = create_fixture(tmp_path / 'spot')
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        repo = await open_file_products(store, capital=values[1], carry=monitor)
        before = await repo.check_carry(binding)
        assert before.allowed and before.funding_reserve_quote > 0
        values[6].advance(1)
        now = int(values[6].wall.timestamp() * 1000)
        refresh(values, feed, payments=(FundingPayment('paid-debit', now, Decimal('-2')),
                                        FundingPayment('paid-credit', now, Decimal('3'))), collateral_quote=Decimal('998'))
        after = await repo.check_carry(binding)
        assert after.settled_payment_quote == Decimal('1')
        assert after.funding_debits_quote == Decimal('2')
        assert after == await repo.check_carry(binding)
        assert after.funding_reserve_quote > 0  # Expected credit never finances admission.
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_actual_hedge_target_port_retains_version_and_idempotency(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator

    from hummingbot.strategy_v2.life_liquidity.storage.products import UpdateHedgeTarget
    hedge, values, _, _ = setup_coordinator(tmp_path / 'native')
    _, binding, owner = create_fixture(tmp_path / 'spot')
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        repo = await open_file_products(store, capital=values[1], hedge=hedge)
        cut = await repo.cut(binding)
        command = UpdateHedgeTarget('target-1', 0, Decimal('11'))
        assert (await repo.update_target(command, cut, owner)).changed
        assert (await repo.update_target(command, cut, owner)).replayed
        assert await repo.hedge_allows_spot(binding)
        with pytest.raises(StorageError, match='PRODUCT_OPERATION_CONFLICT'):
            await repo.update_target(replace(command, target_base_life=Decimal('12')), cut, owner)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_invalid_hedge_target_leaves_store_usable_and_has_no_saga(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator

    from hummingbot.strategy_v2.life_liquidity.storage.products import UpdateHedgeTarget
    hedge, values, _, _ = setup_coordinator(tmp_path / 'native')
    _, binding, owner = create_fixture(tmp_path / 'spot')
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        repo = await open_file_products(store, capital=values[1], hedge=hedge)
        cut = await repo.cut(binding)
        with pytest.raises(StorageError, match='HEDGE_TARGET_VERSION_OR_PENDING'):
            await repo.update_target(UpdateHedgeTarget('bad-version', 99, Decimal('11')), cut, owner)
        assert not store._uncertain
        assert await repo.cut(binding) == cut
        assert not (await store.read(binding)).resources['product_operations']
        assert (await repo.update_target(UpdateHedgeTarget('valid', 0, Decimal('11')), cut, owner)).changed
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_product_cancellation_does_not_block_loop_or_free_physical_capacity(tmp_path, monkeypatch):
    import asyncio
    import threading
    repo, store, owner, _, _ = await fixture(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = repo._capital.reserve

    def blocked(claim):
        entered.set()
        assert release.wait(5)
        return original(claim)

    monkeypatch.setattr(repo._capital, 'reserve', blocked)
    task = asyncio.create_task(repo.reserve(ReserveCapital('cancelled', bound_claim(owner)),
                                            await repo.cut(store.binding), owner))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        # Reaching here proves the loop remains responsive during native work.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._uncertain and len(store._pending) == 1
        with pytest.raises(StorageError, match='STORAGE_CLOSED_OR_UNCERTAIN'):
            await repo.check_capital(store.binding)
    finally:
        release.set()
        await store.close()


@pytest.mark.asyncio
async def test_actual_nav_and_markout_ports_preserve_independent_value_gates(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_capital_risk_binding import _monitor, _nav
    from test.hummingbot.strategy_v2.life_liquidity.test_markout_runtime_binding import _monitor as markout_monitor
    from test.hummingbot.strategy_v2.life_liquidity.test_reconciled_fill_attribution import AT_MS, _attributor
    from test.hummingbot.strategy_v2.life_liquidity.test_runner_fill_events import _setup
    _, _, wal, reservations = _setup(tmp_path / 'native')
    attribution, _ = _attributor(tmp_path / 'native', wal, reservations)
    nav_feed = {'now': AT_MS, 'value': _nav('1.5')}
    risk = _monitor(tmp_path / 'native', attribution, nav_feed)
    markout = markout_monitor(tmp_path / 'native', attribution, {'now': AT_MS, 'observations': {}})
    _, binding, owner = create_fixture(tmp_path / 'spot')
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        repo = await open_file_products(store, risk=risk, markout=markout)
        measurement = await repo.measure_capital(binding)
        assert measurement.adjusted_nav_quote == Decimal('25')
        assert measurement.highwater_quote == Decimal('25')
        assert not await repo.evaluate_markout(binding)  # Insufficient actual horizon samples.
        nav_feed['now'] += 501
        assert await repo.measure_capital(binding) is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_actual_controller_binding_rejects_a_second_capital_cache(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.runtime import FileControllerRuntime
    values = setup_shared(tmp_path / 'native')
    controller, capital = values[:2]
    _, binding, owner = create_fixture(tmp_path / 'spot')
    binding = replace(binding, config_hash=fingerprint(controller.config.strategy.model_dump(mode='json')))
    controller.config = controller.config.model_copy(update={'storage_binding': binding})
    store = await open_store(Backend.FILE, path=tmp_path / 'products.json', binding=binding,
                             limits=CAPACITY, create=True, initial={}, owner=owner, clock=lambda: owner.valid_from)
    try:
        repo = await open_file_products(store, capital=capital)
        runtime = FileControllerRuntime(store, controller, owner, products=repo)
        assert runtime._products is repo
        replacement, _ = setup(tmp_path / 'second-cache')
        controller._shared_capital_authority = replacement
        with pytest.raises(StorageError, match='PRODUCT_CONTROLLER_BINDING_MISMATCH'):
            repo.validate_controller(controller)
        with pytest.raises(StorageError, match='PRODUCT_CONTROLLER_BINDING_MISMATCH'):
            FileControllerRuntime(store, controller, owner, products=repo)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_product_namespaces_cut_expiry_and_session_are_checked_before_native_write(tmp_path):
    repo, store, owner, _, clock = await fixture(tmp_path)
    try:
        cut = await repo.cut(store.binding)
        with pytest.raises(StorageError, match='STORAGE_BINDING_MISMATCH'):
            await repo.cut(replace(store.binding, account_uid='99'))
        wrong = ReserveCapital('wrong-session', claim())
        with pytest.raises(StorageError, match='PRODUCT_SESSION_MISMATCH'):
            await repo.reserve(wrong, cut, owner)
        clock['now'] = owner.expires_at
        with pytest.raises(StorageError, match='PRODUCT_OWNER_REVOKED_OR_EXPIRED'):
            await repo.reserve(ReserveCapital('expired', bound_claim(owner)), cut, owner)
        assert await repo.claims(store.binding) == ()
    finally:
        await store.close()

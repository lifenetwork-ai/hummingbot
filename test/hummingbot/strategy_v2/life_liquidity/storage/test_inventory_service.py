"""Signed inventory mode uses repository fills/budgets and fixed campaign terms."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import quote_service
from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import (
    attach_empty_observer,
    control_policy,
    keys,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import NOW

import pytest

from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionPolicy
from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryMarketObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.inventory_application import InventoryModeCommand
from hummingbot.strategy_v2.life_liquidity.storage.service_config import InventoryServiceConfig


async def inventory_service(tmp_path, *, side='SELL', recovery_probe=False):
    p = InventoryExecutionPolicy(side, D('2'), D('1'), NOW + timedelta(minutes=1), D('1'), D('3'),
                                 D('500'), D('.1'), D('1'), D('.1'), D('1'), D('.01'), D('.1'), D('.1'))
    cfg = InventoryServiceConfig(campaign_id='synthetic-campaign', started_at=NOW, policy=p, max_age_ms=1000)
    signer = keys()
    state, callbacks = sources()
    at = int(NOW.timestamp() * 1000)
    state['inventory'] = InventoryMarketObservation(at, D('.98'), D('1.02'), D('100'), D('0'), D('10'), D('0'), D('0'),
                                                    'independent_market', cfg.campaign_id)
    callbacks['inventory_observation'] = lambda: state['inventory']
    risk, extra = policy(), {}
    if recovery_probe:
        from hummingbot.strategy_v2.life_liquidity.config import ResumePolicyConfig
        state['mono'] = 2.05
        callbacks['monotonic_clock'] = lambda: state['mono']
        risk = policy(stable_data_ms=100, recovery_probe_campaign_base=D('.2'))
        extra['resume_policy'] = ResumePolicyConfig(stable_data_duration='0.1s', probe_size_base='.1')
    service, book = await quote_service(tmp_path, risk_config=risk, risk_sources=callbacks,
                                        control_config=control_policy(signer),
                                        control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                                        inventory_config=cfg, **extra)
    attach_empty_observer(service)
    # Synthetic listing/feed release; native book/reference/rules/fee proofs remain active.
    service.controller._spot_quote_gates_ready = lambda: True
    await service.store._call(service.risk.ready)
    await service.store._call(service.risk.ready)
    return service, state, book, signer


async def activate_inventory(service, signer):
    snapshot = await service.control.snapshot()
    command = InventoryModeCommand('inventory-start', 'INVENTORY', service.config.inventory.campaign_id, fingerprint(service.config.inventory.model_dump(mode='json')))
    approval = signer.sign('operator', service.store.binding, 'INVENTORY', command.json(), snapshot.cut, snapshot.facts,
                           issued_at=service.sources.clock(), expires_at=service.sources.clock() + timedelta(seconds=1))
    return await service.control.apply(command, approval=approval, snapshot=snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_signed_mode_plans_one_bounded_child_from_sole_wallet_without_a_second_attributor(tmp_path, side):
    service, state, book, signer = await inventory_service(tmp_path, side=side)
    try:
        assert not (await activate_inventory(service, signer)).replayed
        actions = await service.plan()
        assert len(actions) == 1, service.inventory.reason_code
        config = actions[0].executor_config
        assert config.side.name == side and config.amount == D('1') and config.timestamp == NOW.timestamp()
        terms = await service.store._call(service.inventory.admission, config)
        assert terms.subsidy_quote_usdt == 0 and terms.reference_price_usdt == D('1')
        assert await service.plan() == []
        assert service.controller._fill_attributor is None
        assert service.inventory.budget is service.store._subsidy
        assert service.store._wal.all_records() == ()
        service.rejected(actions[0])
        await service.drain_action_events()
        assert len(await service.plan()) == 1
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['deadline', 'volume', 'depth', 'depth_inflated', 'anchor', 'stale', 'rules', 'signature'])
async def test_inventory_denies_unqualified_mode_or_child_without_allocating_money(tmp_path, fault):
    from dataclasses import replace
    service, state, book, signer = await inventory_service(tmp_path)
    try:
        if fault == 'signature':
            snapshot = await service.control.snapshot()
            command = InventoryModeCommand('bad-mode', 'INVENTORY', service.config.inventory.campaign_id, fingerprint(service.config.inventory.model_dump(mode='json')))
            approval = signer.sign('operator', service.store.binding, 'INVENTORY', command.json(), snapshot.cut, snapshot.facts,
                                   issued_at=NOW, expires_at=NOW + timedelta(seconds=1))
            with pytest.raises(StorageError):
                await service.control.apply(command, approval=replace(approval, signature='0' * 128), snapshot=snapshot)
        else:
            await activate_inventory(service, signer)
            if fault == 'deadline':
                state['now'] += timedelta(minutes=1)
            elif fault == 'volume':
                state['inventory'] = replace(state['inventory'], known_own_volume_base=D('100'))
            elif fault == 'depth':
                state['inventory'] = replace(state['inventory'], independent_depth_base=D('0'))
            elif fault == 'depth_inflated':
                state['inventory'] = replace(state['inventory'], independent_depth_base=D('1000'))
            elif fault == 'anchor':
                state['inventory'] = replace(state['inventory'], volume_anchor='other-campaign')
            elif fault == 'stale':
                state['inventory'] = replace(state['inventory'], observed_at_ms=state['inventory'].observed_at_ms - 1001)
            else:
                service.controller.listing_gate.instrument_rules = replace(book['snapshot'].rules, lot_size=D('1'))
            assert await service.plan() == []
        assert service.store._wal.all_records() == ()
        view = await service.store.snapshot(service.store.binding)
        assert view.balance.total_quote_usdt == D('10') and view.balance.total_base_life == D('5')
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('change', ['none', 'book', 'risk'])
async def test_inventory_actual_runner_rest_preserves_protected_final_authority(tmp_path, side, change):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_quote_service import (
        test_configured_quote_actual_runner_rest_reprices_after_throttle,
    )
    await test_configured_quote_actual_runner_rest_reprices_after_throttle(tmp_path, change, 'profit_mm', inventory_side=side)


@pytest.mark.asyncio
async def test_partial_terminal_children_retry_only_remaining_campaign_target_without_budget_reset(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.financial import (
        AckSpot,
        FillSpot,
        PrepareSpot,
        SpotFillObservation,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot
    service, state, book, signer = await inventory_service(tmp_path)
    try:
        await activate_inventory(service, signer)
        trade_ids = []
        base, cash = D('5'), D('10')
        for i, quantity in enumerate((D('.4'), D('.8'), D('.8'))):
            action = (await service.plan())[0]
            cfg = action.executor_config
            assert cfg.amount == (D('.8') if i == 2 else D('1'))
            await service.store._call(service.inventory.transition, (cfg.controller_id, cfg.model_dump_json()), 'DISPATCHED')
            terms = await service.store._call(service.inventory.admission, cfg)
            owner = service.spot.owner
            prepare = PrepareSpot('prepare-' + str(i), cfg.id, 'wire' + str(i), cfg.trading_pair, cfg.side.name, cfg.amount,
                                  cfg.price, terms.reference_price_usdt, terms.subsidy_quote_usdt, str(owner.session_id),
                                  owner.epoch, 1, 0, terms.fee_buffer_quote_usdt, terms.fee_buffer_base_life)
            await service.store.prepare_current(prepare, owner=owner)
            await service.store.arm_current(prepare.operation_id, owner=owner)
            exchange_id = str(100 + i)
            await service.store.acknowledge(AckSpot('ack-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id,
                                                    cfg.trading_pair, cfg.side.name, str(owner.session_id), owner.epoch, exchange_id, NOW),
                                            cut=(await service.store.snapshot(service.store.binding)).cut, owner=owner)
            at = int(NOW.timestamp() * 1000)
            event = SpotFillObservation('trade-' + str(i), quantity, cfg.price, 'USDT', D('0'), at, D('1'), at, at, 'independent_market')
            trade_ids.append(event.trade_id)
            fill = FillSpot('fill-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id, cfg.trading_pair,
                            cfg.side.name, str(owner.session_id), owner.epoch, exchange_id, quantity, (event,), NOW)
            await service.store.apply_fills(fill, cut=(await service.store.snapshot(service.store.binding)).cut, owner=owner)
            base -= quantity
            cash += quantity * cfg.price
            facts = SpotTerminalFacts(service.store.binding, NOW, 'FILLED' if quantity == cfg.amount else 'CANCELED', quantity,
                                      cfg.amount, cfg.price, (event,), base, cash, D('0'), D('0'), tuple(trade_ids), (), (), (),
                                      (prepare.client_order_id,), True, True, True)
            terminal = TerminalSpot('terminal-' + str(i), prepare.operation_id, cfg.id, prepare.client_order_id,
                                    cfg.trading_pair, cfg.side.name, str(owner.session_id), owner.epoch, exchange_id, facts)
            await service.store.confirm_terminal(terminal, cut=(await service.store.snapshot(service.store.binding)).cut, owner=owner)
            await service.store._call(service.inventory.recover)
        assert await service.plan() == []
        view = await service.store.snapshot(service.store.binding)
        assert view.balance.total_base_life == D('3') and view.balance.total_quote_usdt == D('12.04')
        assert service.config.inventory.policy.target_base == D('2')
        assert service.config.inventory.policy.deadline_utc == NOW + timedelta(minutes=1)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_checksum_valid_unsigned_mode_change_is_rejected(tmp_path):
    service, _, _, _ = await inventory_service(tmp_path)
    try:
        def forge():
            meta = service.store._read()
            service.store._commit(meta.cut, service.spot.owner, 'synthetic-unsigned-mode',
                                  (('inventory_mode', encode({'mode': 'INVENTORY', 'control_operation': None})),), observation=True)
        await service.store._call(forge)
        with pytest.raises(StorageError):
            await service.store.read(service.store.binding)
        assert await service.plan() == []
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('complete', [True, False])
@pytest.mark.parametrize('interruption', [None, 'native_before', 'native_after'])
async def test_controller_inventory_settlement_uses_repository_cycle_cost_without_refunding_execution_loss(
        tmp_path, monkeypatch, complete, interruption):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command, fill
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import facts, terminal

    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlSnapshot
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
    from hummingbot.strategy_v2.life_liquidity.storage.settlement import SpotSettlementPolicy
    signer = keys()
    service, _ = await quote_service(tmp_path, objective='liquidity_service', control_config=control_policy(signer),
                                     control_seed=lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                                     settlement_policy=SpotSettlementPolicy(1000))
    try:
        store, binding, owner = service.store, service.store.binding, service.spot.owner
        # Two actual domain command/fill/terminal sagas on this service's sole
        # wallet; their independent source fixture reports the expected values.
        for i, side, price, base, cash in ((0, 'SELL', D('.98'), D('4'), D('10.98')),
                                           (1, 'BUY', D('1'), D('5'), D('9.98'))):
            op, intent, wire, exchange = 'netop' + str(i), 'netintent' + str(i), 'netwire' + str(i), str(100 + i)
            await store.prepare_current(order(owner, operation_id=op, intent_id=intent, client_order_id=wire,
                                              side=side, quantity_base=D('1'), limit_price_usdt=price), owner=owner)
            await store.arm_current(op, owner=owner)
            event = fill(trade_id='nettrade' + str(i), quantity_base_life=D('1'), price_usdt=price,
                         signed_fee=D('0'), independent_price_usdt=D('1'))
            value = command(owner, operation_id='netfill' + str(i), prepare_operation_id=op, intent_id=intent,
                            client_order_id=wire, side=side, exchange_order_id=exchange, fills=(event,))
            await store.apply_fills(value, cut=(await store.snapshot(binding)).cut, owner=owner)
            source = facts(binding, exchange_state='FILLED', cumulative_base_life=D('1'), quantity_base_life=D('1'),
                           limit_price_usdt=price, fills=(event,), account_base_life=base, account_quote_usdt=cash,
                           account_trade_ids=tuple('nettrade' + str(v) for v in range(i + 1)), runner_wire_ids=(wire,))
            final = terminal(binding, owner, source, operation_id='netterminal' + str(i), prepare_operation_id=op,
                             intent_id=intent, client_order_id=wire, side=side, exchange_order_id=exchange)
            await store.confirm_terminal(final, cut=(await store.snapshot(binding)).cut, owner=owner)
        before = await store.snapshot(binding)

        async def qualified_snapshot():
            # Source-port fixture; actual HTTP collection/signature/cut fencing
            # is covered by configured control tests, not fabricated from SQL.
            source = SpotFinancialRecoveryFacts(binding, NOW, D('5'), D('9.98'), D('0'), D('0'),
                                                ('nettrade0', 'nettrade1'), (), (), (), True, complete, True)
            snapshot = ControlSnapshot(binding, (await store.snapshot(binding)).cut, source)
            service.control._snapshot = snapshot
            service._control_pending = True
            return snapshot
        monkeypatch.setattr(service.control, 'snapshot', qualified_snapshot)
        if complete:
            if interruption is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
                original = store._subsidy._settle_inventory_cycle

                def crash(*args, **kwargs):
                    if interruption == 'native_after':
                        original(*args, **kwargs)
                    raise OSError('synthetic settlement interruption')
                monkeypatch.setattr(store._subsidy, '_settle_inventory_cycle', crash)
                with pytest.raises(CommitUnknown):
                    await service.controller.settle_service_inventory('net-cycle', ('netintent0', 'netintent1'))
                assert service.runtime._revoked and service._control_pending
                from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageUnavailable
                from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
                with pytest.raises(StorageUnavailable):
                    await store.read(binding)
                args = dict(config=service.config, connectors=service.connectors, sources=service.sources,
                            market_data_provider=service.controller.market_data_provider)
                await service.close()
                service = await FileLiquidityService.open(**args)
                store = service.store
                monkeypatch.setattr(service.control, 'snapshot', qualified_snapshot)
                assert store._cold_unarmed
                pending = await store.read(binding)
                row = next(iter(pending.resources['settlement_operations'].values()))
                assert row['state'] == 'SETTLING'
            assert await service.controller.settle_service_inventory('net-cycle', ('netintent0', 'netintent1'))
            if interruption is not None:
                assert store._cold_unarmed and await service.plan() == []  # No reactivation.
            assert not await service.controller.settle_service_inventory('net-cycle', ('netintent0', 'netintent1'))
            after = await store.snapshot(binding)
            assert after.budget.session_subsidy_committed_usdt == D('.02')
            assert after.budget.session_execution_loss_usdt == D('.02')
            assert after.balance == before.balance
        else:
            with pytest.raises(StorageError):
                await service.controller.settle_service_inventory('net-cycle', ('netintent0', 'netintent1'))
            assert (await store.snapshot(binding)).budget == before.budget
    finally:
        await service.close()

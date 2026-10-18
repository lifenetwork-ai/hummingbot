"""Actual outer File SWAP interruption against an explicit original native seed."""
import asyncio
import json
import time
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import RetireLegacyApplying
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import (
    ImportLegacyApplying,
    PgLegacyApplyingImporter,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, tmp_path, monkeypatch, stage, *, entry=D('1'), fee=D('-.001'), funding=None, cashflow=None, cashflow_written=False, book_native_swap=False, book_native_funding=False, file_result=None, book_native_parent=False, spot_side=None, spot_fee_currency='USDT', spot_terminal_stage=None, spot_settlement_stage=None, native_terminal=True, native_settlement=True):
    from test.hummingbot.strategy_v2.life_liquidity import test_session
    from test.hummingbot.strategy_v2.life_liquidity.storage import test_legacy_joint_applying as file_fixture
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup

    from hummingbot.core.data_type.common import PositionAction, TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot
    from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, CashflowSpot
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import (
        FillSwap,
        PgSwapFillRepository,
        SwapFillObservation,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    from hummingbot.strategy_v2.life_liquidity.storage.settlement import SpotSettlementPolicy
    from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
    admin = database[0]
    now = await admin.fetchval('SELECT clock_timestamp()')
    original_report = file_fixture.report
    pnl = D('.25') * (D('1') - entry)
    final_cash = D('10') + pnl + fee + (funding or D('0'))
    final_base = D('10')
    if spot_side is not None:
        final_base += D('1') if spot_side == 'BUY' else D('-1')
        final_cash += D('-1') if spot_side == 'BUY' else D('1')
        if spot_fee_currency == 'USDT':
            final_cash -= D('.02')
        else:
            final_base -= D('.02')
    if spot_settlement_stage is not None:
        assert spot_fee_currency == 'USDT'
        final_base += D('-1') if spot_side == 'BUY' else D('1')
        final_cash += D('.98') if spot_side == 'BUY' else D('-1.02')
    if cashflow is not None:
        currency, amount, price = cashflow
        final_cash += amount if currency == 'USDT' else D('0')
        final_base += amount if currency == 'LIFE' else D('0')
    approval_at = int(now.timestamp() * 1000)
    final_collateral = final_cash + min(D('0'), D('.75') * (D('1') - entry))

    def clock_init(self):
        self.wall, self.mono = now, 100.0

    def numeric_report(values, box, claim):
        result = original_report(values, box, claim)
        result = replace(result, exchange_order_id='987654321', trades=(replace(result.trades[0], realized_pnl_quote=pnl, signed_fee=fee),))
        stamp = int(values[6].wall.timestamp() * 1000)
        orders = (result,)
        if spot_side is not None:
            from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryTrade
            from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
            prepared, spot_claim = prepared_target['spot']
            leaf = SpotFillObservation('spottrade1', D('1'), D('1'), spot_fee_currency, D('-.02'), stamp,
                                       D('1'), stamp, stamp, 'independent_market')
            prepared_target.setdefault('spot_fill', FillSpot('original-spot-fill', prepared.operation_id, prepared.intent_id,
                                                             prepared.client_order_id, prepared.market, prepared.side, prepared.session_id, prepared.epoch,
                                                             '123456789', D('1'), (leaf,), values[6].wall))
            leaf = prepared_target['spot_fill'].fills[0]
            orders += (RecoveryOrder(spot_claim, '123456789', 'partially_filled' if spot_terminal_stage is None and spot_settlement_stage is None else 'canceled',
                                     (RecoveryTrade('spottrade1', D('1'), D('1'), spot_fee_currency, D('-.02'), D('0'), leaf.fill_at_ms),)),)
        if spot_settlement_stage is not None:
            extra = prepared_target['extra_spot_fills'][0]
            extra_claim = prepared_target['extra_spot'][1]
            orders += (RecoveryOrder(extra_claim, extra.exchange_order_id, 'canceled', tuple(
                RecoveryTrade(f.trade_id, f.quantity_base_life, f.price_usdt, f.fee_currency, f.signed_fee, D('0'), f.fill_at_ms)
                for f in extra.fills)),)
        file_sequence = max(values[8]['snapshot'].sequence, prepared_target.get('file_source_sequence', 1) + 1)
        box['value'] = replace(box['value'], snapshot_sequence=file_sequence, orders=orders, pending_wire_ids=() if spot_side is None or spot_terminal_stage is not None or spot_settlement_stage is not None else ('spotwire',), observed_at_ms=stamp,
                               payments=() if funding is None else (RecoveryPayment('777', funding, stamp),))
        values[7]['value'] = replace(values[7]['value'], snapshot_sequence=file_sequence, observed_at_ms=stamp)
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=file_sequence, observed_at_ms=stamp, spot_cash_quote=final_cash, spot_life_base=final_base, collateral_quote=final_collateral,
                                        pending=() if spot_side is None or spot_terminal_stage is not None or spot_settlement_stage is not None else (prepared_target['spot'][1],))
        return result

    prepared_target = {}

    async def before_report(joint, store, recovery, values, claim, file_owner):
        binding = store.binding
        images = recovery.journal._read()
        anchor = CapitalSnapshot(**{**images['anchor'], **{key: D(images['anchor'][key]) for key in (
            'spot_life_base', 'spot_cash_quote', 'long_base', 'short_base', 'collateral_quote', 'initial_margin_quote',
            'maintenance_margin_quote', 'funding_liability_quote', 'mark_price_usdt', 'index_price_usdt')}, 'pending': ()})
        # An explicitly seeded ORIGINAL target basis/order, not the partial File cash.
        native = await carry_setup(database, max_age_ms=1000, refresh_initial_facts=True,
                                   capital_changes=dict(position_mode='ONEWAY', leverage=1), observed=anchor,
                                   spot_options=dict(original_session_id=file_owner.session_id, opening_base_life=D('10'), config_hash=binding.config_hash,
                                                     configuration_body=lambda _: {'operator_verifier': KEYS.verifier().policy(), 'legacy_source_verifier': KEYS.verifier().policy()}))
        financial, cfg, owner, _, feed, capital, _, carry_feed, carry = native
        # This replay is not a funding-rollover test. Declare a distant future
        # settlement once, before any carry admission; never roll the forecast
        # forward during recovery or hide a missing intervening paid bill.
        old = carry_feed[0]
        settlement = old.funding.observed_at_ms + 4 * 3600000
        carry_feed[0] = replace(old, funding=replace(old.funding, settlement_ms=settlement, next_settlement_ms=settlement + 4 * 3600000))
        policy = dict(controller_id='life', tick_size_usdt='0.0001', min_size_contracts='1', max_quote_levels=8, max_capture_age_ms=1000)
        await admin.execute('INSERT INTO life_financial.swap_policies VALUES($1,$2,$3::jsonb,$4)', cfg.account_id, cfg.binding.policy_hash, json.dumps(policy), fingerprint(policy))
        opening = store._accounting_policy
        await admin.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,10,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, opening.opened_at)
        await admin.execute('INSERT INTO life_financial.swap_fill_policies VALUES($1,$2,1,0,$3,0,64,128,500)', cfg.account_id, cfg.binding.policy_hash, entry)
        await admin.execute('INSERT INTO life_financial.funding_accounting_policies VALUES($1,$2,64,128,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.legacy_applying_policies VALUES($1,$2,4,4194304,5000)', cfg.account_id, cfg.binding.policy_hash)
        if cashflow is not None:
            await admin.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
            await admin.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                                cfg.account_id, '101', currency, amount, approval_at, b'synthetic-preverified-import')
        now = await admin.fetchval('SELECT clock_timestamp()')
        feed[0] = replace(anchor, sequence=2, observed_at_ms=int(now.timestamp() * 1000))
        carry_feed[0] = replace(carry_feed[0], account=replace(carry_feed[0].account, snapshot=feed[0]))
        prefill = True

        async def capture_account():
            # Timestamp the fake response when the repository actually calls
            # its collector, after cut/readback queries, not before those reads.
            if not prefill:
                raise StorageError('SYNTHETIC_PREFILL_SOURCE_CLOSED')
            stamp = int((await admin.fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
            feed[0] = replace(feed[0], sequence=feed[0].sequence + 1, observed_at_ms=stamp)
            # One coherent synthetic response captures account and independent
            # market streams together. Later reads cannot re-date that response.
            old = carry_feed[0]
            carry_feed[0] = replace(old, account=replace(old.account, snapshot=feed[0]),
                                    mark=replace(old.mark, sequence=old.mark.sequence + 1, observed_at_ms=stamp),
                                    index=replace(old.index, sequence=old.index.sequence + 1, observed_at_ms=stamp),
                                    funding=replace(old.funding, sequence=old.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp),
                                    terms=replace(old.terms, sequence=old.terms.sequence + 1, observed_at_ms=stamp))
            return feed[0]

        async def capture_carry():
            if not prefill:
                raise StorageError('SYNTHETIC_PREFILL_SOURCE_CLOSED')
            return carry_feed[0]

        capital.observation, carry.observation = capture_account, capture_carry
        assert (await capital.reserve(ReserveCapital('original-claim', claim), await capital.cut(cfg.binding), owner)).decision.allowed
        # Carry must use the exact independently published capital snapshot.
        # Capture a new fake account response after claim setup, then publish
        # it before taking the carry cut. Never re-date the previous response
        # or change account version from inside the carry collector.
        assert (await capital.check_capital(cfg.binding)).allowed
        assert (await carry.check_carry(cfg.binding)).allowed

        # Capture the order only after its input cut is available. A preceding
        # primary read is setup work, not part of the market capture's lifetime.
        swap = PgSwapRepository(capital)
        prepare_cut = await swap.cut(cfg.binding)
        now = await admin.fetchval('SELECT clock_timestamp()')
        config = OrderExecutorConfig(id=claim.intent_id, timestamp=now.timestamp(), controller_id='life', connector_name='okx_perpetual',
                                     trading_pair='LIFE-USDT', side=TradeType.SELL, position_action=PositionAction.CLOSE, amount=claim.quantity_base,
                                     price=claim.price_usdt, leverage=1, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        prepared = PrepareSwap.from_config('original-prepare', claim.wire_id, config)
        await swap.prepare(prepared, cut=prepare_cut, owner=owner)
        # Do not insert unrelated source-publication transactions between the
        # immutable price capture and ARM. Both commands still enforce the
        # original 1000-ms capture/account limits at entry and physical COMMIT.
        await swap.arm(prepared.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
        await swap.acknowledge(prepared.operation_id, exchange_order_id='987654321', owner=owner)
        if spot_side is not None:
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order

            from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim
            spot = replace(order(file_owner), operation_id='spot-prepare', intent_id='spotintent', client_order_id='spotwire',
                           side=spot_side, risk_epoch=1, subsidy_quote_usdt=D('.5'), fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002'))
            spot_claim = CapitalClaim(spot.intent_id, spot.client_order_id, binding.account_uid, spot.session_id,
                                      spot.epoch, 1, 1, 'SPOT', spot.side, 'OPEN', spot.quantity_base, spot.limit_price_usdt, 1)
            if cashflow is None:
                await admin.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
            assert (await capital.reserve(ReserveCapital('original-spot-claim', spot_claim), await capital.cut(cfg.binding), owner)).decision.allowed
            assert (await capital.check_capital(cfg.binding)).allowed
            assert (await carry.check_carry(cfg.binding)).allowed
            await financial.prepare(spot, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            await financial.arm(spot.operation_id, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert (await store._call(values[1].reserve, spot_claim)).allowed
            await store.prepare(spot, cut=(await store.snapshot(binding)).cut, owner=file_owner)
            await store.arm(spot.operation_id, cut=(await store.snapshot(binding)).cut, owner=file_owner)
            prepared_target['spot'] = spot, spot_claim
            if spot_settlement_stage is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
                now = await admin.fetchval('SELECT clock_timestamp()')
                now = now.replace(microsecond=now.microsecond // 1000 * 1000)
                ack = AckSpot('original-spot-ack', spot.operation_id, spot.intent_id, spot.client_order_id,
                              spot.market, spot.side, spot.session_id, spot.epoch, '123456789', now)
                await financial.acknowledge(ack, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
                await store.acknowledge(replace(ack, observed_at=values[6].wall), cut=(await store.snapshot(binding)).cut, owner=file_owner)
                extra = replace(spot, operation_id='extra-spot-prepare', intent_id='extraspot', client_order_id='extraspotwire',
                                side='SELL' if spot_side == 'BUY' else 'BUY', quote_level=1)
                extra_claim = replace(spot_claim, intent_id=extra.intent_id, wire_id=extra.client_order_id, side=extra.side)
                assert (await capital.reserve(ReserveCapital('extra-spot-claim', extra_claim), await capital.cut(cfg.binding), owner)).decision.allowed
                assert (await capital.check_capital(cfg.binding)).allowed
                assert (await carry.check_carry(cfg.binding)).allowed
                await financial.prepare(extra, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
                await financial.arm(extra.operation_id, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
                assert (await store._call(values[1].reserve, extra_claim)).allowed
                await store.prepare(extra, cut=(await store.snapshot(binding)).cut, owner=file_owner)
                await store.arm(extra.operation_id, cut=(await store.snapshot(binding)).cut, owner=file_owner)
                ack_now = await admin.fetchval('SELECT clock_timestamp()')
                ack_now = ack_now.replace(microsecond=ack_now.microsecond // 1000 * 1000)
                ack_extra = AckSpot('extra-spot-ack', extra.operation_id, extra.intent_id, extra.client_order_id,
                                    extra.market, extra.side, extra.session_id, extra.epoch, '123456790', ack_now)
                await financial.acknowledge(ack_extra, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
                await store.acknowledge(replace(ack_extra, observed_at=values[6].wall),
                                        cut=(await store.snapshot(binding)).cut, owner=file_owner)
                prepared_target['extra_spot'] = extra, extra_claim
        prefill = False
        prepared_target.update(native=native, swap=swap, prepared=prepared, claim=claim)
        captured_now = await admin.fetchval('SELECT clock_timestamp()')
        captured_now = captured_now.replace(microsecond=captured_now.microsecond // 1000 * 1000)
        values[6].wall = captured_now
        values[8]['snapshot'] = replace(values[8]['snapshot'], observed_at_ms=int(captured_now.timestamp() * 1000))
        values[7]['value'] = replace(values[7]['value'], observed_at_ms=int(captured_now.timestamp() * 1000))
        if spot_terminal_stage is not None or spot_settlement_stage is not None:
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen

            from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation
            from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot
            originals = (prepared_target['spot'],) + (() if spot_settlement_stage is None else (prepared_target['extra_spot'],))
            observed_trades = []
            for n, (prepared_spot, _) in enumerate(originals):
                captured_now = await admin.fetchval('SELECT clock_timestamp()')
                captured_now = captured_now.replace(microsecond=captured_now.microsecond // 1000 * 1000)
                values[6].wall = captured_now
                stamp = int(captured_now.timestamp() * 1000)
                trade_id, exchange = ('spottrade1', '123456789') if n == 0 else ('spottrade2', '123456790')
                leaf = SpotFillObservation(trade_id, D('1'), D('1'), spot_fee_currency, D('-.02'), stamp,
                                           D('1'), stamp, stamp, 'independent_market')
                fill = FillSpot('original-spot-fill' if n == 0 else 'extra-spot-fill', prepared_spot.operation_id,
                                prepared_spot.intent_id, prepared_spot.client_order_id, prepared_spot.market, prepared_spot.side,
                                prepared_spot.session_id, prepared_spot.epoch, exchange, D('1'), (leaf,), captured_now)
                assert (await financial.apply_fills(fill, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
                assert (await store.apply_fills(fill, cut=(await store.snapshot(binding)).cut, owner=file_owner)).state == 'APPLIED'
                observed_trades.append(trade_id)
                view = await financial.snapshot(cfg.binding)
                evidence = SpotTerminalFacts(cfg.binding, captured_now, 'CANCELED', D('1'), prepared_spot.quantity_base,
                                             prepared_spot.limit_price_usdt, (leaf,), view.balance.total_base_life, view.balance.total_quote_usdt,
                                             D('0'), D('0'), tuple(observed_trades), (), () if n == len(originals) - 1 else ('extraspotwire',),
                                             () if n == len(originals) - 1 else ('123456790',), (), True, True, True)
                terminal = TerminalSpot('original-spot-terminal' if n == 0 else 'extra-spot-terminal',
                                        prepared_spot.operation_id, prepared_spot.intent_id, prepared_spot.client_order_id,
                                        prepared_spot.market, prepared_spot.side, prepared_spot.session_id, prepared_spot.epoch, exchange, evidence)
                if native_terminal:
                    assert (await financial.confirm_terminal(terminal, cut=view.cut, owner=owner)).state == 'TERMINAL'
                file_terminal = replace(terminal, facts=replace(evidence, binding=binding))
                if spot_terminal_stage is None:
                    await store.confirm_terminal(file_terminal, cut=(await store.snapshot(binding)).cut, owner=file_owner)
                else:
                    target, name = {'observation': (store._wal, 'mark_exchange_terminal_observed'),
                                    'principal': (store._ledger, 'confirm_terminal'), 'wal': (store._wal, 'mark_terminal'),
                                    'receipt': (store, '_publish_terminal')}[spot_terminal_stage]
                    original = getattr(target, name)

                    def interrupted_terminal(*args, **kwargs):
                        if spot_terminal_stage == 'receipt' and not args[0].startswith('term-done:'):
                            return original(*args, **kwargs)
                        if spot_terminal_stage != 'receipt':
                            original(*args, **kwargs)
                        raise OSError('synthetic original pending terminal')
                    with monkeypatch.context() as patch:
                        patch.setattr(target, name, interrupted_terminal)
                        with pytest.raises(CommitUnknown):
                            await store.confirm_terminal(file_terminal, cut=(await store.snapshot(binding)).cut, owner=file_owner)
                prepared_target['spot_fill' if n == 0 else 'extra_spot_fills'] = fill if n == 0 else (fill,)
            if spot_settlement_stage is not None:
                from hummingbot.strategy_v2.life_liquidity.storage.postgres.settlement import (
                    PgInventorySettlementRepository,
                )
                from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory
                command = SettleSpotInventory('original-spot-cycle', 'original-spot-cycle', tuple(p.intent_id for p, _ in originals))
                view = await financial.snapshot(cfg.binding)
                evidence = SpotFinancialRecoveryFacts(cfg.binding, captured_now, view.balance.total_base_life, view.balance.total_quote_usdt,
                                                      D('0'), D('0'), tuple(observed_trades), (), ('spotwire', 'extraspotwire'), (), True, True, True)
                await admin.execute('INSERT INTO life_financial.spot_settlement_policies VALUES($1,$2,1000)', cfg.account_id, cfg.binding.policy_hash)
                if native_settlement:
                    await PgInventorySettlementRepository(financial).settle_inventory(command, facts=evidence, cut=view.cut, owner=owner)
                target, name = (store, '_publish_settlement') if spot_settlement_stage == 'receipt' else (store._subsidy, '_settle_inventory_cycle')
                original = getattr(target, name)

                def interrupted_cycle(*args, **kwargs):
                    if spot_settlement_stage == 'receipt' and not args[0].startswith('settle-done:'):
                        return original(*args, **kwargs)
                    if spot_settlement_stage != 'before':
                        original(*args, **kwargs)
                    raise OSError('synthetic original pending inventory settlement')
                with monkeypatch.context() as patch:
                    patch.setattr(target, name, interrupted_cycle)
                    with pytest.raises(CommitUnknown):
                        await store.settle_inventory(command, facts=replace(evidence, binding=binding),
                                                     cut=(await store.snapshot(binding)).cut, owner=file_owner)
            captured_now = await admin.fetchval('SELECT clock_timestamp()')
            values[6].wall = captured_now.replace(microsecond=captured_now.microsecond // 1000 * 1000)
            return await reopen(tmp_path, store, recovery, values, file_owner)
        if cashflow is not None:
            # Explicit historical valuation at the original transfer time;
            # setup latency cannot move a transfer's original price timestamp.
            flow = CashflowSpot('original-cashflow', '101', currency, amount, approval_at, price, approval_at, approval_at,
                                'quote_unit' if currency == 'USDT' else 'independent_market',
                                captured_now.replace(microsecond=captured_now.microsecond // 1000 * 1000))

            def stop(*_args, **_kwargs):
                if cashflow_written:
                    original_cashflow(*_args, **_kwargs)
                raise OSError('synthetic cashflow interruption before wallet')
            original_cashflow = store._ledger.record_cashflow
            with monkeypatch.context() as patches:
                patches.setattr(store._ledger, 'record_cashflow', stop)
                with pytest.raises(CommitUnknown):
                    await store.apply_cashflow(flow, cut=(await store.snapshot(binding)).cut, owner=file_owner)
            prepared_target['cashflow'] = flow
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
            return await reopen(tmp_path, store, recovery, values, file_owner)

    with monkeypatch.context() as patches:
        patches.setattr(test_session.FakeClock, '__init__', clock_init)
        patches.setattr(file_fixture, 'KEYS', KEYS)
        # The real fixture calls its own module's imported KEYS at creation.
        from test.hummingbot.strategy_v2.life_liquidity.storage import test_joint_cash_repository
        original_fixture = test_joint_cash_repository.fixture

        async def fixture(path, **kwargs):
            from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared
            values = setup_shared(path / 'native', collateral=str(D('10') + min(D('0'), D('1') - entry)), budget_capacity=50)
            approvals = () if cashflow is None else (ApprovedSpotCashflow('101', currency, amount, approval_at),)
            return await original_fixture(path, **{**kwargs, 'operator_keys': KEYS, 'shared_values': values, 'opening_long_entry': entry, 'approvals': approvals, 'spot': spot_side is not None,
                                                   **({} if spot_settlement_stage is None else {'settlement_policy': SpotSettlementPolicy(1000)})})

        patches.setattr(file_fixture, 'fixture', fixture)
        patches.setattr(file_fixture, 'report', numeric_report)
        if spot_side is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.joint import FileJointRepository
            reconcile = FileJointRepository.reconcile

            async def spot_reconcile(repo, binding, owner, **kwargs):
                if 'spot_fill' in prepared_target:
                    old = prepared_target['spot_fill']
                    facts = SpotFinancialRecoveryFacts(binding, repo.store._clock(), final_base, final_cash, D('0'), D('0'),
                                                       ('spottrade1',) + (() if spot_settlement_stage is None else ('spottrade2',)), () if cashflow is None else ('101',),
                                                       ('spotwire',) + (() if spot_settlement_stage is None else ('extraspotwire',)), (), True, True, True)
                    kwargs = dict(spot_fills=(old, *prepared_target.get('extra_spot_fills', ())), spot_facts=facts, **kwargs)
                return await reconcile(repo, binding, owner, **kwargs)
            patches.setattr(FileJointRepository, 'reconcile', spot_reconcile)
        joint, store, recovery, values, file_owner = await file_fixture.interrupted(tmp_path, patches, stage, before_report=before_report)
    if file_result is not None:
        # Fork only this disposable File fixture at its exact crash bytes.
        # Complete the actual File recovery as the independent money oracle,
        # then restore the crash branch before retirement. Native facts and
        # the permanent source-retirement fence are never rewound.
        images = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        options = {} if spot_side is None else dict(spot_fills=(prepared_target['spot_fill'], *prepared_target.get('extra_spot_fills', ())),
                                                    spot_facts=SpotFinancialRecoveryFacts(store.binding, values[6].wall, final_base, final_cash, D('0'), D('0'),
                                                                                          ('spottrade1',) + (() if spot_settlement_stage is None else ('spottrade2',)), () if cashflow is None else ('101',),
                                                                                          ('spotwire',) + (() if spot_settlement_stage is None else ('extraspotwire',)), (), True, True, True))
        assert await joint.reconcile(store.binding, file_owner, **options), recovery.reason_code
        expected = await store.snapshot(store.binding)
        file_result.update(balance=expected.balance, budget=expected.budget)
        await store.close()
        for p in tmp_path.rglob('*'):
            if p.is_file() and p not in images:
                p.unlink()
        for p, raw in images.items():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
        joint, store, recovery, values = await reopen(tmp_path, store, recovery, values, file_owner)
    native, swap, prepared, claim = (prepared_target[k] for k in ('native', 'swap', 'prepared', 'claim'))
    financial, cfg, owner, _, feed, capital, _, carry_feed, carry = native
    binding = store.binding
    at = recovery.clock_ms()
    trade = SwapFillObservation('trade-1', D('.25'), D('1'), 'USDT', fee, at, D('1'), at, at, 'independent_market', pnl, None)
    fill = FillSwap('original-fill', prepared.operation_id, claim.intent_id, claim.wire_id, 'LIFE-USDT', claim.side,
                    claim.session_id, claim.epoch, '987654321', D('.25'), (trade,), values[6].wall)
    if book_native_swap and not book_native_parent:
        # Received fills remain accountable under HALT. Pin that explicit
        # stopped state before capturing the cut; a live source TTL crossing
        # must not turn this recovery fixture into a transient-ready retry.
        await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        native_cut = (await financial.observation_cut(cfg.binding)).cut
        received = await admin.fetchval('SELECT clock_timestamp()')
        original_fill = replace(fill, operation_id='native-prior-swap', observed_at=received)
        assert (await PgSwapFillRepository(swap).apply_fills(original_fill, cut=native_cut, owner=owner)).state == 'APPLIED'
    fresh_now = await admin.fetchval('SELECT clock_timestamp()')
    values[6].wall = fresh_now
    facts = SpotFinancialRecoveryFacts(binding, fresh_now, final_base, final_cash, D('0'), D('0'), ('trade-1',) + (() if spot_side is None else ('spottrade1',)) + (() if spot_settlement_stage is None else ('spottrade2',)),
                                       () if cashflow is None else ('101',), (claim.wire_id,) + (() if spot_side is None else ('spotwire',)) + (() if spot_settlement_stage is None else ('extraspotwire',)), (), True, True, True)
    drain = OwnerTransition('outer-drain', OwnerMode.DRAINING, file_owner)
    cut = (await store.snapshot(binding)).cut
    signature = KEYS.sign('operator', binding, 'DRAIN', drain.json(), cut, facts, issued_at=fresh_now, expires_at=fresh_now + timedelta(seconds=10))
    await store.transition_owner(drain, approval=signature, cut=cut, facts=facts)
    cut = (await store.snapshot(binding)).cut
    source = await store.legacy_applying_source(binding, facts=facts, cut=cut, joint=joint)
    retirement = RetireLegacyApplying('outer-retire', source.content_digest, cfg.binding, cfg.database_lineage)
    signed = KEYS.sign('operator', binding, 'IMPORT', retirement.json(), cut, facts, issued_at=fresh_now, expires_at=fresh_now + timedelta(seconds=10))
    await store.retire_legacy_applying(retirement, source=source, approval=signed, joint=joint)
    original_cut = None
    if book_native_parent:
        # The independent File resume oracle may outlive the original carry
        # response. Establish the original accounting batch on an explicitly
        # halted, expired-readiness cut, then collect its own new response.
        # Crossing that earlier TTL must not alter the cut during application.
        await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        current = await admin.fetchval('SELECT clock_timestamp()')
        deadline = max(feed[0].observed_at_ms, carry_feed[0].mark.observed_at_ms,
                       carry_feed[0].index.observed_at_ms, carry_feed[0].funding.observed_at_ms,
                       carry_feed[0].terms.observed_at_ms) + 1001
        await asyncio.sleep(max(0, deadline - current.timestamp() * 1000) / 1000)
        original_cut = (await financial.observation_cut(cfg.binding)).cut
        fresh_now = await admin.fetchval('SELECT clock_timestamp()')
    b = carry_feed[0]
    stamp = int(fresh_now.timestamp() * 1000)
    snapshot = replace(b.account.snapshot, sequence=b.account.snapshot.sequence + 1, observed_at_ms=stamp, spot_cash_quote=final_cash, spot_life_base=final_base,
                       long_base=D('.75'), collateral_quote=final_collateral)
    b = replace(b, account=replace(b.account, snapshot=snapshot, included_funding_bill_ids=() if funding is None else ('777',)), mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=stamp),
                index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=stamp), funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp),
                terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=stamp))
    if funding is not None:
        b = replace(b, funding=replace(b.funding, payments=(FundingPayment('777', at, funding),)))
    flows = () if cashflow is None else (prepared_target['cashflow'],)
    if book_native_parent:
        original_parent = ApplyJointBatch('native-prior-parent', (),
                                          (replace(fill, operation_id='native-prior-swap'),),
                                          tuple(replace(v, operation_id='native-prior-transfer') for v in flows),
                                          ApplyFunding('native-prior-funding', b))
        assert (await PgJointBatchRepository(swap).apply(original_parent, cut=original_cut, owner=owner)).state == 'APPLIED'
    elif book_native_funding:
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
        assert (await PgFundingRepository(swap).apply_funding(ApplyFunding('native-prior-funding', b),
                                                              cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    batch = PgJointBatchRepository(swap)
    cmd = ImportLegacyApplying(source.content_digest, ApplyJointBatch('outer-pending', () if spot_side is None else (prepared_target['spot_fill'], *prepared_target.get('extra_spot_fills', ())), (fill,), flows, ApplyFunding('outer-funding', b)))
    await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
    # This is a halted import, not source-readiness renewal. Capture its signed
    # diagnostic cut after the original pre-fill carry source has expired. Do
    # not sign a transient ready digest just as its 1000-ms source TTL crosses.
    now = await admin.fetchval('SELECT clock_timestamp()')
    retained = await admin.fetchval("""SELECT COALESCE(max(greatest(
        (bundle#>>'{account,snapshot,observed_at_ms}')::bigint,(bundle#>>'{mark,observed_at_ms}')::bigint,
        (bundle#>>'{index,observed_at_ms}')::bigint,(bundle#>>'{funding,observed_at_ms}')::bigint,
        (bundle#>>'{terms,observed_at_ms}')::bigint)),0) FROM (
        SELECT bundle FROM life_financial.funding_observations WHERE account_id=$1
        UNION ALL SELECT bundle FROM life_financial.carry_checks WHERE account_id=$1) captured""", cfg.account_id)
    deadline = max(retained, feed[0].observed_at_ms, carry_feed[0].mark.observed_at_ms,
                   carry_feed[0].index.observed_at_ms, carry_feed[0].funding.observed_at_ms,
                   carry_feed[0].terms.observed_at_ms) + 1001
    await asyncio.sleep(max(0, deadline - now.timestamp() * 1000) / 1000)
    cut = (await financial.observation_cut(cfg.binding)).cut
    facts = replace(facts, binding=cfg.binding, observed_at=await admin.fetchval('SELECT clock_timestamp()'))
    approval = KEYS.sign('operator', cfg.binding, 'IMPORT', cmd.json(), cut, facts, issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=10))
    importer = PgLegacyApplyingImporter(admin, batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
    return native, batch, cmd, cut, facts, approval, importer, store, source


async def apply_recovery(database, batch, command, facts, store):
    financial, cfg, owner = batch.financial, batch.runtime.config, batch.owner
    b = command.batch.funding.bundle
    # Establish the unchanged halted accounting cut before collecting the new
    # fake provider response. Reading the cut is not a source refresh and must
    # not consume the response's 1000-ms validity window unnecessarily.
    repair_cut = (await financial.observation_cut(cfg.binding)).cut
    previous = await database[0].fetchval("""SELECT COALESCE(max(greatest(
        (bundle#>>'{account,snapshot,sequence}')::bigint,(bundle#>>'{mark,sequence}')::bigint,
        (bundle#>>'{index,sequence}')::bigint,(bundle#>>'{funding,sequence}')::bigint,
        (bundle#>>'{terms,sequence}')::bigint)),0) FROM (
        SELECT bundle FROM life_financial.funding_observations WHERE account_id=$1
        UNION ALL SELECT bundle FROM life_financial.carry_checks WHERE account_id=$1) captured""", cfg.account_id)
    sequence = max(previous, b.account.snapshot.sequence, b.mark.sequence, b.index.sequence, b.funding.sequence, b.terms.sequence) + 1
    now = await database[0].fetchval('SELECT clock_timestamp()')
    captured_monotonic = time.monotonic()
    at = int(now.timestamp() * 1000)
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=sequence, observed_at_ms=at)),
                mark=replace(b.mark, sequence=sequence, observed_at_ms=at), index=replace(b.index, sequence=sequence, observed_at_ms=at),
                funding=replace(b.funding, sequence=sequence, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=sequence, observed_at_ms=at))
    repair = ResolveJointSources('outer-recover', command.operation_id,
                                 replace(command.batch, operation_id='outer-applied', funding=replace(command.batch.funding, operation_id='outer-funding-applied', bundle=b)))
    proof = JointRepairFacts(replace(facts, observed_at=now), tuple((product, f.trade_id) for product, groups in (('SPOT', command.batch.spot_fills), ('SWAP', command.batch.swap_fills)) for group in groups for f in group.fills), tuple(p.bill_id for p in command.batch.funding.bundle.funding.payments), True,
                             int(store._accounting_policy.opened_at.timestamp() * 1000), at)
    signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
    try:
        await PgJointRepairApprovalImporter(database[0], batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(repair, approval=signed, cut=repair_cut, facts=proof)
        result = await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)
    except StorageError as error:
        # Failure-only synthetic timing, without another source capture,
        # retry or deadline change. Host/VM suspension may advance primary
        # wall time while Python's elapsed test clock barely advances.
        try:
            async with asyncio.timeout(2):
                failed_at = await database[0].fetchval('SELECT clock_timestamp()')
            error.add_note('Synthetic repair timing: primary_elapsed_ms='
                           f'{(failed_at - now).total_seconds() * 1000:.3f}, '
                           f'monotonic_elapsed_ms={(time.monotonic() - captured_monotonic) * 1000:.3f}, '
                           'max_fact_age_ms=5000; original signed facts were not refreshed')
        except Exception:
            error.add_note('Synthetic repair timing unavailable; original failure retained')
        raise
    return result, repair, repair_cut


@pytest.mark.asyncio
@pytest.mark.parametrize('written', [False, True])
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'cash_done'])
@pytest.mark.parametrize('currency,amount,price', [('USDT', D('2'), D('1')), ('USDT', D('-2'), D('1')),
                                                   ('LIFE', D('2'), D('1.2')), ('LIFE', D('-2'), D('1.2'))])
async def test_retired_mixed_source_recovers_original_external_transfer_swap_and_funding_once(database, tmp_path, monkeypatch, stage, currency, amount, price, written):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, funding=D('-.1'), cashflow=(currency, amount, price), cashflow_written=written)
    financial, cfg, _ = values[:3]
    try:
        held = await values[5].claims(cfg.binding)
        budget = (await financial.snapshot(cfg.binding)).budget
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED' and not result.trading_permission
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=batch.owner)).replayed
        view = await financial.snapshot(cfg.binding)
        assert (view.balance.total_base_life, view.balance.total_quote_usdt) == (facts.account_base_life, facts.account_quote_usdt)
        assert view.budget.session_execution_loss_usdt == budget.session_execution_loss_usdt + D('.001')
        assert await values[5].claims(cfg.binding) == held
        for table in ('cashflows', 'swap_fill_facts', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 1
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        raw = await database[0].fetchrow('SELECT independent_price FROM life_financial.cashflows')
        assert raw['independent_price'] == price
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['bill', 'amount', 'currency', 'time', 'price', 'value_time', 'reference_time', 'receipt_time',
                                   'omitted', 'extra', 'scope', 'native_approval'])
async def test_new_import_signature_cannot_rewrite_retired_original_cashflow_facts_or_approval(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, _, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', funding=D('-.1'), cashflow=('LIFE', D('2'), D('1.2')))
    financial, cfg, _ = values[:3]
    try:
        old = command.batch.cashflows[0]
        changes = dict(bill=dict(bill_id='102'), amount=dict(signed_amount=D('1')), currency=dict(currency='USDT', independent_price_usdt=D('1'), source_kind='quote_unit'),
                       time=dict(transfer_at_ms=old.transfer_at_ms + 1), price=dict(independent_price_usdt=D('2')),
                       value_time=dict(value_at_ms=old.value_at_ms - 1), reference_time=dict(reference_observed_at_ms=old.reference_observed_at_ms + 1),
                       receipt_time=dict(observed_at=old.observed_at + timedelta(microseconds=1)))
        flows = (replace(old, **changes.get(fault, {})),)
        if fault == 'omitted':
            flows = ()
        elif fault == 'extra':
            flows += (replace(old, operation_id='extra-cashflow', bill_id='102'),)
        elif fault == 'scope':
            facts = replace(facts, account_cashflow_ids=())
        elif fault == 'native_approval':
            await database[0].execute('ALTER TABLE life_financial.bill_approvals DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.bill_approvals SET signed_amount=1 WHERE account_id=$1', cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.bill_approvals ENABLE TRIGGER USER')
        command = replace(command, batch=replace(command.batch, cashflows=flows))
        cut = (await financial.observation_cut(cfg.binding)).cut
        now = await database[0].fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_'):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'cashflows', 'swap_fill_facts', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_old_source_approval_is_permanent_but_a_future_independent_bill_does_not_invalidate_it(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(
        database, tmp_path, monkeypatch, 'cash_done', cashflow=('USDT', D('2'), D('1')))
    financial, cfg, _ = values[:3]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,$5,$6)',
                                  cfg.account_id, '102', 'USDT', D('1'), facts.observed_at, b'synthetic-future-approval')
        assert (await importer.lookup(command.operation_id)).state == 'RECOVERY_REQUIRED'
        await apply_recovery(database, batch, command, facts, store)
        assert (await importer.lookup(command.operation_id)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('11.999')
        await database[0].execute('ALTER TABLE life_financial.bill_approvals DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.bill_approvals SET signed_amount=3 WHERE account_id=$1 AND bill_id='101'", cfg.account_id)
        await database[0].execute('ALTER TABLE life_financial.bill_approvals ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,entry,fee,funding', [(s, D('1'), D('-.001'), None) for s in
                                                     ('outer_before_cash', 'wallet_after', 'cash_done', 'wal_after', 'shared_after')]
                         + [('wallet_after', D('1.02'), D('-.001'), D('-.1')), ('cash_done', D('.98'), D('-.001'), D('.04')),
                            ('shared_after', D('1.02'), D('.001'), None)])
async def test_outer_file_swap_import_then_recover_keeps_original_trade_session_basis_and_gross_fee(database, tmp_path, monkeypatch, stage, entry, fee, funding):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch, stage, entry=entry, fee=fee, funding=funding)
    financial, cfg, owner = values[:3]
    try:
        held = await values[5].claims(cfg.binding)
        assert not (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED' and not result.trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == facts.account_quote_usdt
        assert (await financial.balance(cfg.binding)).total_base_life == 10
        assert await values[5].claims(cfg.binding) == held
        loss = max(D('0'), -D('.25') * (D('1') - entry)) + max(D('0'), -fee)
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE account_id=$1 AND family='LOSS' AND scope='SESSION'", cfg.account_id) == loss
        source_loss = json.loads(dict(source.journal_images)['loss'])['events']
        if stage in ('cash_done', 'wal_after', 'shared_after'):
            assert sum((D(e['loss_quote']) for e in source_loss.values()), D('0')) == loss
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == (0 if funding is None else 1)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert (await importer.lookup(command.operation_id)).state == 'RECOVERY_REQUIRED'
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'pnl', 'quantity', 'price', 'trade_time', 'trade_id', 'wire', 'exchange', 'session', 'side', 'prepare', 'basis'])
async def test_even_a_signed_target_import_cannot_change_original_swap_economics_or_order_basis(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch, 'wallet_after')
    financial, cfg, _ = values[:3]
    try:
        fill = command.batch.swap_fills[0]
        observed = fill.fills[0]
        edits = dict(fee=dict(signed_fee=D('-.002')), pnl=dict(realized_pnl_usdt=D('.001')),
                     quantity=dict(quantity_base_life=D('.2')), price=dict(price_usdt=D('1.01')),
                     trade_time=dict(fill_at_ms=observed.fill_at_ms + 1), trade_id=dict(trade_id='different-trade'))
        if fault in edits:
            fill = replace(fill, fills=(replace(observed, **edits[fault]),),
                           cumulative_base_life=D('.2') if fault == 'quantity' else fill.cumulative_base_life)
        elif fault in ('wire', 'exchange', 'session', 'side', 'prepare'):
            fill = replace(fill, **dict(wire=dict(client_order_id='DifferentWire'), exchange=dict(exchange_order_id='123456789'),
                                        session=dict(session_id=str(UUID(int=999))), side=dict(side='BUY'), prepare=dict(prepare_operation_id='different-prepare'))[fault])
        else:
            await database[0].execute('ALTER TABLE life_financial.swap_fill_policies DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.swap_fill_policies SET opening_long_entry=2 WHERE account_id=$1', cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.swap_fill_policies ENABLE TRIGGER USER')
            cut = (await financial.observation_cut(cfg.binding)).cut
        command = replace(command, batch=replace(command.batch, swap_fills=(fill,)))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        signed = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_'):
            await importer.import_pending(command, source_store=store, approval=signed, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'swap_fill_facts', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_changed_retained_swap_basis_with_rehashed_body_blocks_common_history_and_cold_import_audit(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch, 'cash_done')
    financial, cfg, _ = values[:3]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body=jsonb_set(body,'{source_basis,swap_opening,opening_long_entry}',to_jsonb('2'::text)) WHERE account_id=$1", cfg.account_id)
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body_hash=encode(sha256(convert_to(body::text,'UTF8')),'hex') WHERE account_id=$1", cfg.account_id)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_UNVERIFIED'):
            await batch.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_outer_pending_process_exit_then_cold_recovery_books_swap_fee_once_without_releasing_claim(database, tmp_path, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_process import exit_pending

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, command, _, initial_facts, _, importer, store, source = await setup(database, tmp_path, monkeypatch, 'cash_done')
    financial, cfg, owner = values[:3]
    cold = None
    try:
        async def capture():
            cut = (await financial.observation_cut(cfg.binding)).cut
            now = await database[0].fetchval('SELECT clock_timestamp()')
            facts = replace(initial_facts, observed_at=now)
            approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
            await importer._approve(command, store, approval, cut, facts)
            return cut, facts, approval

        cut, facts, approval = await exit_pending(cfg, database[2], owner, command, KEYS.verifier(), committed, capture)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        batch = PgJointBatchRepository(PgSwapRepository(capital))
        restored = PgLegacyApplyingImporter(database[0], batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        assert ((await restored.lookup(command.operation_id)) is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        assert (await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED'
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.999')
        assert len(await capital.claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert not result.trading_permission
    finally:
        if cold is not None:
            await cold.close()
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'shared_after'])
@pytest.mark.parametrize('funding', [None, D('-.1'), D('.04')])
async def test_native_swap_and_funding_prefix_recovery_does_not_charge_original_facts_twice(database, tmp_path, monkeypatch, stage, funding):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    file_result = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, entry=D('1.02'), funding=funding,
        book_native_swap=True, book_native_funding=funding is not None, file_result=file_result)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        held = await values[5].claims(cfg.binding)
        before = await financial.balance(cfg.binding)
        budget = await financial.budget(cfg.binding)
        originals = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id IN ('native-prior-swap','native-prior-funding')", cfg.account_id)
        assert before.total_quote_usdt == facts.account_quote_usdt
        assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        assert await financial.balance(cfg.binding) == before
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.lookup(command.operation_id)).replayed
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED' and not result.trading_permission
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert await cold.balance(cfg.binding) == before == file_result['balance']
        assert await cold.budget(cfg.binding) == budget == file_result['budget']
        assert await capital.claims(cfg.binding) == held
        assert await c.fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == int(funding is not None)
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id IN ('native-prior-swap','native-prior-funding')", cfg.account_id) == originals
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('cashflow', [('USDT', D('2'), D('1')), ('LIFE', D('-2'), D('1.2'))])
async def test_original_native_swap_cashflow_funding_parent_preserves_all_children_during_file_recovery(database, tmp_path, monkeypatch, cashflow):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
    expected_file = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', entry=D('1.02'), funding=D('-.1'),
        cashflow=cashflow, cashflow_written=True, book_native_parent=True, file_result=expected_file)
    financial, cfg, owner = values[:3]
    cold = None
    c = database[0]
    try:
        held, money, budget = await values[5].claims(cfg.binding), await financial.balance(cfg.binding), await financial.budget(cfg.binding)
        originals = await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id LIKE 'native-prior-%'", cfg.account_id)
        assert money.total_quote_usdt == facts.account_quote_usdt
        assert money.total_base_life == facts.account_base_life
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await importer.lookup(command.operation_id)).replayed
        _, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert await cold.balance(cfg.binding) == money == expected_file['balance']
        assert await cold.budget(cfg.binding) == budget == expected_file['budget']
        assert await capital.claims(cfg.binding) == held
        assert (await batch.lookup('native-prior-parent', cfg.binding)).state == 'APPLIED'
        assert await c.fetchval("SELECT jsonb_agg(to_jsonb(o) ORDER BY operation_id) FROM life_financial.operations o WHERE account_id=$1 AND operation_id LIKE 'native-prior-%'", cfg.account_id) == originals
        for table in ('spot_cashflow_facts', 'swap_fill_facts', 'funding_cash_payments'):
            assert await c.fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', cfg.account_id) == 1
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()
        if cold is not None:
            await cold.close()

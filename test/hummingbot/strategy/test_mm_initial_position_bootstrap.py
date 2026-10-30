"""Managed MM initial exposure must not replace durable post-fill holds."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import PositionMode, PositionSide, TradeType
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.position import Position
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
from hummingbot.strategy_v2.models.position_config import InitialPositionConfig


def held(side, amount, fee='0'):
    return Position(id=side, controller_id='maker', connector_name='okx_perpetual',
                    trading_pair='BTC-USDT', side=side, amount=Decimal(amount),
                    timestamp=1000, breakeven_price=Decimal('78686.5'),
                    volume_traded_quote=Decimal('20'), cum_fees_quote=Decimal(fee),
                    unrealized_pnl_quote=Decimal('0'), realized_pnl_quote=Decimal('0'))


def setup(monkeypatch, stored, *, managed=True):
    recorder = MagicMock()
    recorder.iter_executor_performance.side_effect = lambda *_: (x for x in ())
    recorder.get_all_positions.side_effect = lambda controller_ids: [p for p in stored if p.controller_id in controller_ids]
    monkeypatch.setattr(MarketsRecorder, 'get_instance', lambda: recorder)
    config = SimpleNamespace(id='maker', mm_session_id='active-session' if managed else None,
                             initial_positions=[InitialPositionConfig(connector_name='okx_perpetual',
                                                                      trading_pair='BTC-USDT', side=side,
                                                                      amount=Decimal(amount))
                                                for side, amount in ((TradeType.BUY, '.0081'), (TradeType.SELL, '.0022'))])
    provider = MagicMock()
    provider.get_price_by_type.return_value = Decimal('78686.5')
    connector = SimpleNamespace(name='okx_perpetual', position_mode=PositionMode.HEDGE,
                                account_positions={side: SimpleNamespace(trading_pair='BTC-USDT', position_side=side,
                                                                         amount=Decimal(amount))
                                                   for side, amount in ((PositionSide.LONG, '.0084'), (PositionSide.SHORT, '.0027'))})
    strategy = SimpleNamespace(config=SimpleNamespace(load_controller_configs=lambda: [config], mm_drawdown_scope=None),
                               controllers={'maker': SimpleNamespace(config=config)}, connectors={'okx_perpetual': connector},
                               markets={'okx_perpetual': {'BTC-USDT'}}, market_data_provider=provider,
                               current_timestamp=100, logger=lambda: MagicMock())
    return strategy, connector, config, recorder


def restore(strategy):
    initial = StrategyV2Base._collect_initial_positions(strategy)
    strategy.executor_orchestrator = ExecutorOrchestrator(strategy, initial_positions_by_controller=initial)
    return strategy.executor_orchestrator


def test_postfill_restart_uses_durable_quantity_and_original_fees(monkeypatch):
    strategy, connector, _, _ = setup(monkeypatch, [held('BUY', '.0084', '.0047181294'),
                                                    held('SELL', '.0027', '.00786827')])
    restored = restore(strategy)
    BotOwnership._verify_positions(SimpleNamespace(strategy=strategy), connector)
    positions = restored.positions_held['maker']
    assert {p.side: abs(p.net_amount_base) for p in positions} == {TradeType.BUY: Decimal('.0084'), TradeType.SELL: Decimal('.0027')}
    assert sum((p.cum_fees_quote for p in positions), Decimal(0)) == Decimal('.0125863994')


def test_empty_database_still_bootstraps_reviewed_initial_exposure(monkeypatch):
    strategy, connector, _, _ = setup(monkeypatch, [])
    connector.account_positions[PositionSide.LONG].amount = Decimal('.0081')
    connector.account_positions[PositionSide.SHORT].amount = Decimal('.0022')
    restored = restore(strategy)
    BotOwnership._verify_positions(SimpleNamespace(strategy=strategy), connector)
    assert len(restored.positions_held['maker']) == 2


def test_durable_zero_does_not_revive_old_yaml_seed(monkeypatch):
    strategy, connector, _, _ = setup(monkeypatch, [held('BUY', '0')])
    connector.account_positions = {}
    restored = restore(strategy)
    BotOwnership._verify_positions(SimpleNamespace(strategy=strategy), connector)
    assert all(p.net_amount_base == 0 for p in restored.positions_held['maker'])


def test_partial_durable_snapshot_cannot_fill_missing_leg_from_yaml(monkeypatch):
    strategy, connector, _, _ = setup(monkeypatch, [held('BUY', '.0084')])
    restored = restore(strategy)
    with pytest.raises(SubmissionBlocked, match='positions do not match'):
        BotOwnership._verify_positions(SimpleNamespace(strategy=strategy), connector)
    assert len(restored.positions_held['maker']) == 1
    assert restored.positions_held['maker'][0].net_amount_base == Decimal('.0084')


@pytest.mark.asyncio
async def test_failed_reconciliation_cleanup_preserves_durable_exposure_and_fees(monkeypatch):
    strategy, connector, _, recorder = setup(monkeypatch, [held('BUY', '.0084', '.0047181294'),
                                                           held('SELL', '.0027', '.00786827')])
    connector.account_positions[PositionSide.LONG].amount = Decimal('.0099')
    restored = restore(strategy)
    with pytest.raises(SubmissionBlocked):
        BotOwnership._verify_positions(SimpleNamespace(strategy=strategy), connector)
    await restored.stop(keep_position=True)
    stored = [c.args[0] for c in recorder.update_or_store_position.call_args_list]
    assert {p.side: p.amount for p in stored} == {'BUY': Decimal('.0084'), 'SELL': Decimal('.0027')}
    assert sum((p.cum_fees_quote for p in stored), Decimal(0)) == Decimal('.0125863994')


def test_unmanaged_upstream_override_semantics_and_no_extra_query(monkeypatch):
    strategy, _, config, recorder = setup(monkeypatch, [held('BUY', '.0084')], managed=False)
    assert StrategyV2Base._collect_initial_positions(strategy) == {'maker': config.initial_positions}
    recorder.get_all_positions.assert_not_called()


def test_managed_bootstrap_lookup_is_batched_and_controller_specific(monkeypatch):
    strategy, _, config, recorder = setup(monkeypatch, [held('BUY', '.0084')])
    new = SimpleNamespace(id='new', mm_session_id='active-session', initial_positions=config.initial_positions)
    strategy.config.load_controller_configs = lambda: [config, new]
    assert StrategyV2Base._collect_initial_positions(strategy) == {'new': new.initial_positions}
    recorder.get_all_positions.assert_called_once_with(controller_ids=['maker', 'new'])


def test_position_read_failure_is_not_treated_as_an_empty_bootstrap(monkeypatch):
    strategy, _, _, recorder = setup(monkeypatch, [])
    recorder.get_all_positions.side_effect = OSError('Position storage unavailable')
    with pytest.raises(OSError, match='Position storage unavailable'):
        StrategyV2Base._collect_initial_positions(strategy)

"""Fresh component assembly from durable files; only external feeds survive."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.offline_market import install_market
from test.hummingbot.strategy_v2.life_liquidity.test_final_quote_send import _attach_quote_planner
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import cold_restore
from types import SimpleNamespace

from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryMonitor
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.executor_send import ProtectedSpotExecutorSender
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionLedger
from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryExecutor
from hummingbot.strategy_v2.life_liquidity.joint_exposure import JointExposureObservation, LinearLifeContractSpec
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.order_gateway import SpotAccountReconciler
from hummingbot.strategy_v2.life_liquidity.protected_send import ProtectedSpotGateway
from hummingbot.strategy_v2.life_liquidity.request_budget import AccountRequestBudget
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate
from hummingbot.strategy_v2.life_liquidity.telemetry import TelemetryRecorder


async def restore_assembly(f):
    """No journal creation, reused controller state, authorization or rearm."""
    old = f.c
    joint, v = cold_restore(f.joint, f.v)
    c, pool, sender, spot, swap, _, clock, _, _, strategy, _, _ = v
    del c.allow_create_executor_actions  # Remove the component fixture shortcut.
    old_request = f.v[2].request_budget
    request = AccountRequestBudget(old_request.path, account_uid=old_request.account_uid,
                                   policy=old_request.policy, clock=old_request.clock)
    sender.request_budget = request
    c._order_safety_gateway.request_budget = None  # Same explicit spot policy as initial assembly.
    boundary = ProtectedSpotGateway(spot, c._order_safety_wal, authorize=lambda _: False)
    original_sender = old._protected_spot_sender
    c.install_protected_spot_sender(ProtectedSpotExecutorSender(
        c, boundary, c._order_safety_reservations, risk_epoch=original_sender.risk_epoch,
        authorize=original_sender.policy_authorize, reference_price=original_sender.reference_price))
    await install_market(c, spot, exchange_ms=int(clock.wall.timestamp() * 1000))
    original_config = c.config
    _attach_quote_planner(c, SimpleNamespace(_strategy=strategy), c._order_safety_wal,
                          c._order_safety_reservations, propose=False)
    c.config = original_config
    c._runner_orchestrator.get_stored_executors_by_controller = lambda _: ()
    gate = old._runtime_risk_gate
    c.install_runtime_risk_gate(SafetyGate(
        gate.path, max_drawdown_bps=gate.max_drawdown_bps, min_margin_buffer_quote=gate.min_margin_buffer_quote,
        stable_data_ms=gate.stable_data_ms, recovery_probe_base=gate.recovery_probe_base, require_existing=True),
        observation=old._runtime_risk_observation, monotonic_clock_ms=old._runtime_risk_clock_ms,
        max_observation_age_ms=old._runtime_risk_max_age_ms)
    original = old._execution_loss_budget
    loss = LossBudgetLedger(original.path, campaign_id=original.campaign_id,
                            campaign_limit_quote=original.campaign_limit_quote, day_limit_quote=original.day_limit_quote,
                            session_limit_quote=original.session_limit_quote)
    c.install_execution_loss_budget(loss, utc_clock=lambda: clock.wall)
    previous = old._fill_attributor
    original = previous.exit_budget
    budget = SubsidyBudgetLedger(original.path, campaign_id=original.campaign_id,
                                 campaign_limit_quote=original.campaign_limit_quote, day_limit_quote=original.day_limit_quote,
                                 session_limit_quote=original.session_limit_quote)
    attribution = ReconciledFillAttributor(
        previous.path, wal=c._order_safety_wal, reservations=c._order_safety_reservations, loss_budget=loss,
        opening_life=previous.opening_life, opening_usdt=previous.opening_usdt,
        opening_independent_price_usdt=previous.opening_independent_price_usdt,
        independent_value=previous.independent_value, max_reference_skew_ms=previous.max_reference_skew_ms,
        create=False, exit_budget=budget)
    c._order_safety_gateway.account_check = SpotAccountReconciler(spot, c._order_safety_reservations).check
    c.install_fill_attributor(attribution)
    quote = c._quote_action_planner
    snapshot = quote.snapshot()
    quote.snapshot = lambda: replace(snapshot, loss_budget_status=c.execution_loss_status)

    def exposure():
        obs = pool.qualified_snapshot()
        reservations = c._order_safety_reservations.preview()
        claims = pool.claims().values()
        return JointExposureObservation(
            c._order_safety_reservations.life_balance,
            reservations.unresolved_quantity_base("BUY"), reservations.unresolved_quantity_base("SELL"),
            (obs.long_base - obs.short_base) / D(".25"),
            sum((x.quantity_base / D(".25") for x in claims if x.product == "SWAP" and x.side == "BUY"), D("0")),
            sum((x.quantity_base / D(".25") for x in claims if x.product == "SWAP" and x.side == "SELL"), D("0")),
            obs.mark_price_usdt, obs.index_price_usdt, obs.mark_source, obs.index_source,
            obs.collateral_quote, obs.maintenance_margin_quote, obs.funding_liability_quote)

    c.perpetual_contract = sender.contract
    c.install_joint_risk_gate(LinearLifeContractSpec(D(".25"), D(".1")), pool.policy.limits, observation=exposure)
    carry = CarryMonitor(f.carry.journal.path, policy=f.carry.policy, capital=pool,
                         observation=f.carry.observation, clock_ms=f.carry.clock_ms, create=False)
    c.install_carry_monitor(carry)
    original = f.inventory.ledger
    ledger = InventoryExecutionLedger(original.path, session_id=original.session_id, policy=original.policy, create=False)
    inventory = InventoryExecutor(c, f.inventory.journal.path, ledger=ledger, budget=budget,
                                  observation=f.inventory.observation, clock_ms=f.inventory.clock_ms,
                                  max_age_ms=f.inventory.max_age_ms, create=False)
    c.install_inventory_executor(inventory)
    log = TelemetryRecorder(f.log.path, clock_ms=f.log.clock_ms, synthetic=True, max_records=10000, create=False)
    # Telemetry observation is external I/O; the recorder itself is reopened.
    c.install_telemetry(log, independent_value=old._telemetry_value,
                        utc_clock_ms=lambda: int(clock.wall.timestamp() * 1000), max_value_age_ms=1000)
    return SimpleNamespace(c=c, v=v, joint=joint, inventory=inventory, hedge=c._hedge_coordinator,
                           carry=carry, log=log)

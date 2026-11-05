from decimal import Decimal
from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from hummingbot.cli.credential_disconnect import DisconnectCoordinator
from hummingbot.connector.credential_adapters import CredentialAdapterError
from hummingbot.model.bot_state import BotState
from hummingbot.model.position import Position

managed = managed_fixture


class Runtime:
    def __init__(self, store, actual):
        self.store, self.actual = store, actual
        self.calls = []
        self.partial_once = False
        self.detached = False

    async def prepare(self, operation):
        self.calls.append("prepare")

    async def cancel_orders(self):
        self.calls.append("cancel")

    async def positions(self):
        return dict(self.actual)

    async def close_position(self, position, operation):
        self.calls.append((position["id"], position["amount"]))
        side = "LONG" if position["side"] == "BUY" else "SHORT"
        amount = position["amount"] / 2 if self.partial_once else position["amount"]
        remaining = self.actual[(position["trading_pair"], side)] - amount
        with self.store.engine.begin() as connection:
            connection.execute(update(Position).where(Position.id == position["id"]).values(amount=remaining))
        if remaining:
            self.actual[position["trading_pair"], side] = remaining
        else:
            self.actual.pop((position["trading_pair"], side))
        if self.partial_once:
            self.partial_once = False
            raise CredentialAdapterError("exchange_unavailable", status=503)

    async def shutdown(self):
        self.calls.append("shutdown")

    async def detach(self, connector):
        self.detached = True


def positions(store):
    with store.engine.begin() as connection:
        for side in ("BUY", "SELL"):
            connection.execute(Position.__table__.insert().values(id=side, controller_id="controller", connector_name="okx_perpetual",
                                                                  side=side, trading_pair="BTC-USDT", timestamp=1, amount=Decimal("0.1"), breakeven_price=100,
                                                                  volume_traded_quote=10, realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=0))


@pytest.mark.asyncio
async def test_spot_disconnect_closes_both_hedge_sides_then_removes_only_spot(managed):
    store, actor = loaded(managed, both=True)
    positions(store)
    receipt = disconnect(store, actor, control=2)
    runtime = Runtime(store, {("BTC-USDT", "LONG"): Decimal("0.1"), ("BTC-USDT", "SHORT"): Decimal("0.1")})
    coordinator = DisconnectCoordinator(store, SimpleNamespace(owner_uuid="owner", generation=1), runtime)
    assert await coordinator.run(receipt["id"]) is True
    assert ("BUY", Decimal("0.1")) in runtime.calls and ("SELL", Decimal("0.1")) in runtime.calls
    assert runtime.calls.index("cancel") < runtime.calls.index(("BUY", Decimal("0.1")))
    assert runtime.detached and store.load("okx", "okx", "demo")["envelope"] is None
    assert store.load("okx", "okx_perpetual", "demo")["envelope"] is not None


@pytest.mark.asyncio
async def test_partial_close_retry_uses_remaining_checkpoint_and_never_repeats_full_size(managed):
    store, actor = loaded(managed, both=True)
    positions(store)
    receipt = disconnect(store, actor, control=2)
    runtime = Runtime(store, {("BTC-USDT", "LONG"): Decimal("0.1"), ("BTC-USDT", "SHORT"): Decimal("0.1")})
    runtime.partial_once = True
    coordinator = DisconnectCoordinator(store, SimpleNamespace(owner_uuid="owner", generation=1), runtime)
    assert await coordinator.run(receipt["id"]) is False
    assert not runtime.detached and store.load("okx", "okx", "demo")["envelope"] is not None
    assert store.operation(receipt["id"])["status"] == "applying"
    assert await coordinator.run(receipt["id"]) is True
    assert runtime.calls.count(("BUY", Decimal("0.1"))) == 1 and ("BUY", Decimal("0.05")) in runtime.calls


@pytest.mark.asyncio
async def test_unmatched_account_exposure_is_not_closed_or_falsely_reported_disconnected(managed):
    store, actor = loaded(managed, both=True)
    positions(store)
    receipt = disconnect(store, actor, control=2)
    runtime = Runtime(store, {("BTC-USDT", "LONG"): Decimal("0.2"), ("BTC-USDT", "SHORT"): Decimal("0.1")})
    coordinator = DisconnectCoordinator(store, SimpleNamespace(owner_uuid="owner", generation=1), runtime)
    assert await coordinator.run(receipt["id"]) is False
    operation = store.operation(receipt["id"])
    assert operation["status"] == "applying" and operation["result"]["operator_action_required"] is True
    assert not any(isinstance(call, tuple) for call in runtime.calls)
    assert store.load("okx", "okx", "demo")["envelope"] is not None


@pytest.mark.asyncio
async def test_accepted_cleanup_continues_after_actor_disable_and_new_owner_restart(managed):
    from hummingbot.core.admin_identity import AccessDenied
    from hummingbot.model.admin_access import AdminAccount
    store, actor = loaded(managed, both=True)
    positions(store)
    receipt = disconnect(store, actor, control=2)
    runtime = Runtime(store, {('BTC-USDT', 'LONG'): Decimal('0.1'), ('BTC-USDT', 'SHORT'): Decimal('0.1')})
    runtime.partial_once = True
    assert not await DisconnectCoordinator(store, SimpleNamespace(owner_uuid='owner', generation=1), runtime).run(receipt['id'])
    current = store.operation(receipt['id'])
    with store.engine.begin() as connection:
        connection.execute(update(AdminAccount).where(AdminAccount.id == actor.id).values(active=False, version=3))
        connection.execute(update(BotState).values(owner_uuid='replacement', owner_generation=2))
    with pytest.raises(AccessDenied):
        store.retry_disconnect(actor, receipt['id'], expected_version=1, expected_control_version=3,
                               expected_operation_version=current['result']['operation_version'], idempotency_key='disabled-retry', request_id='disabled')
    restarted = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='replacement', generation=2), runtime)
    assert await restarted.run(receipt['id'])
    assert runtime.calls.count(('BUY', Decimal('0.1'))) == 1
    assert store.operation(receipt['id'])['status'] == 'succeeded'


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['waiting_for_owner', 'checking_account', 'cancelling_orders', 'closing_positions', 'verifying', 'detaching'])
async def test_durable_cleanup_boundaries_recover_under_a_new_owner_without_erasing_early(managed, stage):
    store, actor = loaded(managed)
    receipt = disconnect(store, actor)
    proof = dict(orders_terminal_verified=True, account_verified=True, position_checkpoints_verified=True,
                 observed_at=float(store.store.read()['database_now']), owner_generation=1)
    if stage != 'waiting_for_owner':
        assert store.progress_disconnect(receipt['id'], 'owner', 1, stage=stage, evidence=proof)
    assert store.load('okx', 'okx', 'demo')['envelope'] is not None
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid='replacement', owner_generation=2))
    assert not store.finish_disconnect(receipt['id'], 'owner', 1, evidence=proof)
    restarted = DisconnectCoordinator(store, SimpleNamespace(owner_uuid='replacement', generation=2), Runtime(store, {}))
    assert await restarted.run(receipt['id'])
    assert store.load('okx', 'okx', 'demo')['envelope'] is None
    assert store.operation(receipt['id'])['result']['owner_generation'] == 2

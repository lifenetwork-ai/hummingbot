"""A failed/cancelled new account refresh cannot leave the old runtime admitted."""
import asyncio
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['capital', 'carry'])
async def test_cancelled_product_source_revokes_local_runtime_and_retains_original_claim(database, product):
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = await carry_setup(database, max_age_ms=10000)
    command = ReserveCapital('held', native_claim(owner, amount='1'))
    assert (await capital.reserve(command, await capital.cut(cfg.binding), owner)).decision.allowed
    assert (await carry.check_carry(cfg.binding)).allowed
    repository = capital if product == 'capital' else carry
    entered = asyncio.Event()

    async def waiting():
        entered.set()
        await asyncio.Future()

    repository.observation = waiting
    task = None
    try:
        before = await financial.snapshot(cfg.binding)
        task = asyncio.create_task(capital.check_capital(cfg.binding) if product == 'capital' else carry.check_carry(cfg.binding))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not financial._runtime.healthy
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await capital.claims(cfg.binding) == (command.claim,)
        with pytest.raises(StorageError, match='PG_CLOSED_OR_UNQUALIFIED'):
            await financial._runtime._admit(lambda c: c.fetchval('SELECT 1'), safety=False)
    finally:
        if task is not None and not task.done():
            task.cancel()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['exception', 'type', 'wire_type'])
async def test_failed_account_source_revokes_local_runtime_before_any_new_financial_command(database, fault):
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = await carry_setup(database, max_age_ms=10000)

    def failed():
        if fault == 'exception':
            raise ConnectionError('synthetic account refresh unavailable')
        if fault == 'wire_type':
            return replace(account_feed[0], spot_cash_quote=10.0)
        return None

    capital.observation = failed
    try:
        before = await financial.snapshot(cfg.binding)
        with pytest.raises((StorageError, ConnectionError)):
            await capital.check_capital(cfg.binding)
        assert not financial._runtime.healthy
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await capital.claims(cfg.binding) == ()
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['account', 'balance', 'sequence'])
async def test_rejected_new_shared_account_evidence_cannot_be_replaced_by_old_ready_state(database, fault):
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = await carry_setup(database, max_age_ms=10000)
    try:
        before = await financial.snapshot(cfg.binding)
        changes = {'account': dict(account_uid='999'), 'balance': dict(spot_cash_quote=account_feed[0].spot_cash_quote + 1),
                   'sequence': dict(collateral_quote=account_feed[0].collateral_quote + 1)}[fault]
        account_feed[0] = replace(account_feed[0], **changes)
        with pytest.raises(StorageError):
            await capital.check_capital(cfg.binding)
        assert not financial._runtime.healthy
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await capital.claims(cfg.binding) == ()
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_diagnostic_recheck_after_source_failure_cannot_heal_or_allocate_on_old_runtime(database):
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = await carry_setup(database, max_age_ms=10000)

    def failed():
        raise ConnectionError('synthetic source unavailable')

    capital.observation = failed
    try:
        before = await financial.balance(cfg.binding)
        with pytest.raises(ConnectionError):
            await capital.check_capital(cfg.binding)
        capital.observation = lambda: account_feed[0]
        assert (await capital.check_capital(cfg.binding)).allowed  # Diagnostic, no grant.
        assert not financial._runtime.healthy
        with pytest.raises(StorageError, match='PG_CLOSED_OR_UNQUALIFIED'):
            await capital.reserve(ReserveCapital('new', native_claim(owner, amount='1')), await capital.cut(cfg.binding), owner)
        assert await financial.balance(cfg.binding) == before
        assert await capital.claims(cfg.binding) == ()
    finally:
        await financial.close()

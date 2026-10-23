"""Owned primary cuts for received truth; explicit caller cuts stay strict."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import (
    ack,
    filled,
    sent,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
async def test_received_original_survives_same_version_carry_expiry_without_refreshing_facts(database, monkeypatch, kind):
    values = await carry_setup(database, max_age_ms=4000, refresh_initial_facts=True)
    financial, cfg, owner, _, _, capital, _, feed, carry = values
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        claim = native_claim(owner, 'intent1', amount='2', price='1')
        claim = replace(claim, wire_id='wire1')
        assert (await capital.reserve(ReserveCapital('spotclaim', claim), await capital.cut(cfg.binding), owner)).decision.allowed
        assert (await carry.check_carry(cfg.binding)).allowed
        await financial.prepare(order(owner, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002')),
                                cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        original_source = feed[0]
        before, claims = await financial.snapshot(cfg.binding), await capital.claims(cfg.binding)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        command = ack(owner, at) if kind == 'ACK' else filled(owner, at)
        capture, cuts = financial.observation_cut, []

        async def cross_original_expiry(binding):
            value = await capture(binding)
            cuts.append(value)
            if len(cuts) == 1:
                assert await database[0].fetchval('SELECT life_financial.carry_current_ready($1)', cfg.account_id)
                deadline = min(original_source.account.snapshot.observed_at_ms, original_source.mark.observed_at_ms,
                               original_source.index.observed_at_ms, original_source.funding.observed_at_ms,
                               original_source.terms.observed_at_ms) + 4000
                now = await database[0].fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
                assert now < deadline, ('carry expired before the tested boundary', now, deadline, value.observed_at)
                await asyncio.sleep(max(0, (deadline - now) / 1000) + .1)
                current = await financial.snapshot(binding)
                assert current.cut.checkpoint.revision == value.cut.checkpoint.revision
                # Migration 91 deliberately excludes time-derived readiness
                # from the persistent cut; expiry changes permission, not truth.
                assert current.cut.checkpoint.state_digest == value.cut.checkpoint.state_digest
                assert current.recovery_required
                assert not await database[0].fetchval('SELECT life_financial.carry_current_ready($1)', cfg.account_id)
            return value

        monkeypatch.setattr(financial, 'observation_cut', cross_original_expiry)
        receipt = await financial.apply_received(command, owner=owner)
        assert receipt.state == ('ACKED' if kind == 'ACK' else 'APPLIED')
        assert len(cuts) == 1 and feed[0] == original_source
        after = await financial.snapshot(cfg.binding)
        if kind == 'ACK':
            assert (after.balance, after.budget) == (before.balance, before.budget)
        else:
            assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (D('6'), D('8.98'))
            assert after.budget.session_execution_loss_usdt == receipt.execution_loss_usdt
        assert after.recovery_required and await capital.claims(cfg.binding) == claims
        assert (await financial.apply_received(command, owner=owner)).replayed
        assert (await financial.snapshot(cfg.binding)).balance == after.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2', cfg.account_id, command.operation_id) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_explicit_untrusted_cut_is_never_upgraded_by_received_observation_support(database):
    financial, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        view = await financial.snapshot(cfg.binding)
        fake = replace(view.cut, checkpoint=replace(view.cut.checkpoint, state_digest='f' * 64), components=(('postgres_account', 'f' * 64),))
        with pytest.raises(StorageError, match='PG_SPOT_CUT_CONFLICT'):
            await financial.apply_fills(filled(owner, at), cut=fake, owner=owner)
        assert (await financial.snapshot(cfg.binding)).balance == view.balance
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_received_cut_rejection_after_real_write_retains_original_failure_without_second_application(database, monkeypatch):
    financial, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        command = filled(owner, at)
        capture, cuts = financial.observation_cut, []

        async def concurrent_ack(binding):
            value = await capture(binding)
            cuts.append(value)
            if len(cuts) == 1:
                await financial.acknowledge(ack(owner, at, operation_id='concurrent-ack'), cut=value.cut, owner=owner)
            return value

        monkeypatch.setattr(financial, 'observation_cut', concurrent_ack)
        with pytest.raises(StorageError, match='PG_EXPECTED_VERSION_CONFLICT'):
            await financial.apply_received(command, owner=owner)
        assert len(cuts) == 1
        assert await financial.lookup_fill(command.operation_id, cfg.binding) is None
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_received_observation_never_retries_uncertain_commit(database, monkeypatch, committed):
    financial, cfg, owner = await sent(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        command = filled(owner, at)
        original, commits, cuts = financial._runtime._commit, [], []
        capture = financial.observation_cut

        async def read(binding):
            value = await capture(binding)
            cuts.append(value)
            return value

        async def lose_reply(tx):
            commits.append(True)
            if committed:
                await original(tx)
            raise OSError('synthetic received observation COMMIT reply loss')

        monkeypatch.setattr(financial, 'observation_cut', read)
        monkeypatch.setattr(financial._runtime, '_commit', lose_reply)
        with pytest.raises(CommitUnknown):
            await financial.apply_received(command, owner=owner)
        assert len(cuts) == len(commits) == 1
    finally:
        await financial.close()

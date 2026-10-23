"""Complete stopped service ancestry includes referenced segments, never rearm."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration_service import sign
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_handoff_source import LIMITS, staged

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.service_source import StoppedFileServiceSource


@pytest.mark.asyncio
async def test_stopped_ordinary_source_is_complete_revalidated_and_keeps_money(tmp_path):
    service, _ = await staged(tmp_path)
    try:
        before = await service.store.snapshot(service.store.binding)
        cut = await service.control.stopped_postgres_source(**LIMITS)
        assert type(cut) is StoppedFileServiceSource and cut.archives == ()
        assert cut.base.core.cut == before.cut
        assert (await service.control.validate_stopped_postgres_source(cut, **LIMITS)).content_digest == cut.content_digest
        assert (await service.store.snapshot(service.store.binding)).balance == before.balance
        assert not service.runtime._local_ready()
        # The old File target workflow must not silently discard the new graph.
        with pytest.raises(StorageError, match='SOURCE_INVALID'):
            await service.control.validate_financial_configuration_service_source(cut, **LIMITS)
    finally:
        await service.close()


async def joint_staged(tmp_path, *, flat=True):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_control import control_policy, keys
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import joint_service

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
    existing = keys()
    signer = OperatorKeys(existing.keys, {key: roles | frozenset(('CONFIGURE',)) for key, roles in existing.roles.items()})

    def extra(config, sources):
        result = {'control_seed': lambda b, o: encode(signer.seed(b, {'mode': 'ACTIVE', 'token': o.json()})),
                  'joint_spot_recovery': lambda: ((), replace(facts(config.controller.storage_binding), observed_at=sources.clock()))}
        if flat:
            capital = replace(sources.capital_snapshot(), long_base=Decimal(0), short_base=Decimal(0), funding_liability_quote=Decimal(0))
            account = replace(sources.swap_account(), net_contracts=Decimal(0), long_contracts=Decimal(0), short_contracts=Decimal(0))
            carry = sources.carry_observation()
            carry = replace(carry, account=replace(carry.account, snapshot=capital))
            result.update(capital_snapshot=lambda: capital, swap_account=lambda: account, carry_observation=lambda: carry)
        return result
    service, _, _, _, _ = await joint_service(tmp_path, carry=True, config_extra={'control': control_policy(signer)}, source_extra=extra)
    command, snapshot = await service.control.propose_financial_configuration({'session': {'duration': '2h'}}, operation_id='joint-source')
    await service.control.apply(command, approval=sign(service, signer, command, snapshot), snapshot=snapshot)
    return service


@pytest.mark.asyncio
async def test_segmented_joint_capture_includes_all_terminal_history_without_accepting_active_claims(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.test_checkpoint_archive import capacity
    service = await joint_staged(tmp_path)
    try:
        capital = service.joint._recovery.capital.journal
        await service.store._call(capital.migrate_archive, capacity())
        before = await service.store.snapshot(service.store.binding)
        cut = await service.control.stopped_postgres_source(**LIMITS)
        assert set(dict(cut.base.ancillary_images)) == {'shared_capital', 'requests', 'swap_wal', 'swap_actions', 'joint_recovery', 'hedge', 'carry'}
        assert set(dict(cut.archives)) == {'ancillary.shared_capital'}
        archive = dict(cut.archives)['ancillary.shared_capital']
        assert archive.root == capital.path.read_bytes()
        assert json.loads(archive.root)['schema_version'] == 2
        assert (await service.control.validate_stopped_postgres_source(cut, **LIMITS)).content_digest == cut.content_digest
        after = await service.store.snapshot(service.store.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert not service.runtime._local_ready() and not service.swap.sender._runtime_enabled
        with pytest.raises(StorageError, match='ARCHIVE_UNSUPPORTED'):
            await service.control.financial_configuration_service_source(**LIMITS)
        # Captured roots cannot be paired with a missing archive descriptor.
        with pytest.raises(StorageError):
            replace(cut, archives=())
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_stopped_source_rejects_open_swap_position_even_with_empty_order_book(tmp_path):
    service = await joint_staged(tmp_path, flat=False)
    try:
        with pytest.raises(StorageError, match='NOT_QUIESCENT'):
            await service.control.stopped_postgres_source(**LIMITS)
        assert not service.runtime._local_ready()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_stopped_revalidation_cannot_refresh_expired_facts_or_append_recovery(tmp_path, monkeypatch):
    service, _ = await staged(tmp_path)
    try:
        cut = await service.control.stopped_postgres_source(**LIMITS)

        async def no_recovery():
            raise AssertionError('frozen validation must not reconcile or append')
        monkeypatch.setattr(service.control, 'snapshot', no_recovery)
        assert await service.control.validate_stopped_postgres_source(cut, **LIMITS) == cut
        old = service.store._clock()
        monkeypatch.setattr(service.store, '_clock', lambda: old + timedelta(hours=1))
        with pytest.raises(StorageError):
            await service.control.validate_stopped_postgres_source(cut, **LIMITS)
        assert not service.runtime._local_ready()
    finally:
        await service.close()

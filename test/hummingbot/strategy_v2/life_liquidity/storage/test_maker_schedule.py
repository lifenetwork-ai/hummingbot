"""Loop ordering, stop ownership and unknown-state containment without an exchange."""
import asyncio
from types import SimpleNamespace

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.maker_schedule import PgMakerLoop, PgMakerSchedule


def schedule(**changes):
    return PgMakerSchedule(**dict(dict(schedule_id='synthetic', levels=(0,), refresh_interval_ms=1, max_cycles=2), **changes))


@pytest.mark.asyncio
async def test_each_generation_retires_before_next_and_never_reuses_an_identity():
    events = []

    async def quote(intent, side, level):
        events.append(('quote', intent, side.name, level))
        return SimpleNamespace(executor_config=SimpleNamespace(id=intent))

    async def cancel(targets):
        events.append(('retire', targets))
        return targets
    loop = PgMakerLoop(schedule(), namespace='account-owner', quote=quote, cancel=cancel, ready=lambda: True)
    await loop.start()
    assert [e[0] for e in events] == ['quote', 'quote', 'retire'] * 2
    assert [e[2] for e in events if e[0] == 'quote'] == ['BUY', 'SELL'] * 2
    assert len({e[1] for e in events if e[0] == 'quote'}) == 4
    assert loop.completed_cycles == 2 and loop.targets == ()
    with pytest.raises(StorageError, match='ALREADY_STARTED'):
        loop.start()


@pytest.mark.asyncio
async def test_stop_during_original_quote_joins_then_retires_without_creating_ask():
    entered, release = asyncio.Event(), asyncio.Event()
    events = []

    async def quote(intent, side, level):
        entered.set()
        await release.wait()
        events.append(('quote', intent))
        return SimpleNamespace(executor_config=SimpleNamespace(id=intent))

    async def cancel(targets):
        events.append(('retire', targets))
        return targets
    loop = PgMakerLoop(schedule(), namespace='account-owner', quote=quote, cancel=cancel, ready=lambda: True)
    loop.start()
    await entered.wait()
    stop = asyncio.create_task(loop.stop())
    await asyncio.sleep(0)
    assert not stop.done()
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop
    release.set()
    await loop.stop()
    assert [e[0] for e in events] == ['quote', 'retire'] and not loop.targets


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['quote_unknown', 'retire_unknown', 'retire_incomplete', 'revoked'])
async def test_failure_or_revocation_never_advances_to_a_new_generation(fault):
    events, ready = [], [True]

    async def quote(intent, side, level):
        events.append('quote')
        if fault == 'quote_unknown':
            raise StorageError('unknown original submit')
        if fault == 'revoked':
            ready[0] = False
        return SimpleNamespace(executor_config=SimpleNamespace(id=intent))

    async def cancel(targets):
        events.append('retire')
        if fault == 'retire_unknown':
            raise StorageError('unknown original retirement')
        return () if fault == 'retire_incomplete' else targets
    loop = PgMakerLoop(schedule(), namespace='account-owner', quote=quote, cancel=cancel, ready=lambda: ready[0])
    task = loop.start()
    if fault == 'revoked':
        await task
        assert events == ['quote'] and len(loop.targets) == 1
    else:
        with pytest.raises(StorageError):
            await task
        assert events == (['quote'] if fault == 'quote_unknown' else ['quote', 'quote', 'retire'])
        assert loop.failure is not None and loop.completed_cycles == 0
        assert len(loop.targets) == (1 if fault == 'quote_unknown' else 2)


@pytest.mark.asyncio
async def test_owner_budget_refusal_retires_the_bid_without_starting_a_sibling():
    events = []

    async def quote(intent, side, level):
        events.append('quote')
        return SimpleNamespace(executor_config=SimpleNamespace(id=intent))

    async def cancel(targets):
        events.append('retire')
        return targets
    loop = PgMakerLoop(schedule(), namespace='account-owner', quote=quote, cancel=cancel,
                       ready=lambda: True, can_quote=lambda remaining: not events)
    await loop.start()
    assert events == ['quote', 'retire'] and loop.targets == () and loop.completed_cycles == 0


@pytest.mark.parametrize('changes', [{'levels': (True,)}, {'levels': (0, 0)}, {'levels': ()},
                                     {'refresh_interval_ms': 0}, {'refresh_interval_ms': True}, {'max_cycles': 0}])
def test_schedule_has_no_implicit_or_unbounded_calibration(changes):
    with pytest.raises(StorageError):
        schedule(**changes)

"""Immutable, bounded action commands without runtime/network dependencies."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.actions import (
    ClaimQuoteActions,
    RunnerActionFacts,
    SpotQuoteAction,
    TransitionQuoteAction,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, StorageError


def action():
    return SpotQuoteAction('intent', 'controller', 'session', 1, 1, 'LIFE-USDT', 'BUY', 0, '{ "captured":true }')


def facts():
    binding = StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', uuid4(), 1, 'a' * 64, 'b' * 64)
    return RunnerActionFacts(binding, datetime(2026, 10, 16, tzinfo=timezone.utc), 'controller', (), True)


@pytest.mark.parametrize('changes', [
    dict(intent_id=''), dict(controller_id=None), dict(session_id='invalid id'), dict(epoch=True), dict(epoch=0),
    dict(epoch=2**63), dict(config_version=-1), dict(config_version=2**63), dict(level=True), dict(level=-1),
    dict(level=2**31), dict(market='ETH-USDT'), dict(side='buy'), dict(payload_json='[]'), dict(payload_json='{}'),
    dict(payload_json=''), dict(payload_json='bad'), dict(payload_json='{"x":NaN}'),
    dict(payload_json='{"x":"' + 'x' * 65536 + '"}'), dict(payload_json='{"x":"\ud800"}'),
])
def test_invalid_action_denied(changes):
    with pytest.raises(StorageError):
        replace(action(), **changes)


@pytest.mark.parametrize('rows', [(), [], (None,), (action(), action()),
                                  (action(), replace(action(), intent_id='other'))])
def test_invalid_or_duplicate_batch_denied(rows):
    with pytest.raises(StorageError):
        ClaimQuoteActions('claim', rows)


@pytest.mark.parametrize('changes', [dict(executor_ids=['one']), dict(executor_ids=('one', 'one')),
                                     dict(executor_ids=(None,)), dict(executor_ids=({},)), dict(scope_complete=1),
                                     dict(observed_at=datetime(2026, 10, 16)), dict(binding=None)])
def test_untyped_runner_facts_denied(changes):
    with pytest.raises(StorageError):
        replace(facts(), **changes)


@pytest.mark.parametrize('expected,state', [('DISPATCHED', 'REJECTED'), ('REJECTED', 'PROPOSED'), ('PROPOSED', 'PREPARED'), ('PREPARED', 'REJECTED'), ('RECONCILED', 'PROPOSED')])
def test_impossible_action_transition_denied(expected, state):
    with pytest.raises(StorageError):
        TransitionQuoteAction('change', action(), expected, state, facts())


def test_commands_are_frozen_capture_exact_bytes_and_copy_json():
    row = action()
    command = ClaimQuoteActions('claim', (row,))
    value = command.json()
    assert value['actions'][0]['payload_json'] == '{ "captured":true }'
    value['actions'][0]['payload_json'] = '{}'
    assert command.actions[0].payload_json == '{ "captured":true }'
    with pytest.raises(FrozenInstanceError):
        row.side = 'SELL'
    transition = TransitionQuoteAction('reject', row, 'PROPOSED', 'REJECTED', facts())
    assert transition.json()['facts']['scope_complete'] is True


def test_prepared_action_can_request_qualified_terminal_reconciliation():
    command = TransitionQuoteAction('close', action(), 'PREPARED', 'RECONCILED', facts())
    assert command.json()['expected_state'] == 'PREPARED'
    assert command.facts.scope_complete is True

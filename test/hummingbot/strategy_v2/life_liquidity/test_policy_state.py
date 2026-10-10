"""Immutable risk policy and atomic, stale-writer-safe runtime checkpoints."""

from unittest.mock import patch

import pytest

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState


def test_policy_restore_rejects_changed_limits_stale_writers_and_clock_rollback(tmp_path):
    path = tmp_path / "policy.json"
    first = PolicyState(path, policy={"limit": "1"}, initial={"checked_at_ms": 1}, create=True)
    stale = PolicyState(path, policy={"limit": "1"}, initial={}, create=False)
    with first.locked() as state:
        first.commit({**state, "checked_at_ms": 2})
    with pytest.raises(ValueError):
        with stale.locked():
            pass
    with pytest.raises(ValueError):
        PolicyState(path, policy={"limit": "2"}, initial={}, create=False)
    restored = PolicyState(path, policy={"limit": "1"}, initial={}, create=False)
    with restored.locked() as state:
        assert state["checked_at_ms"] == 2


def test_uncertain_policy_checkpoint_blocks_same_instance(tmp_path):
    journal = PolicyState(tmp_path / "policy.json", policy={}, initial={"n": 0}, create=True)
    with journal.locked():
        with patch("hummingbot.strategy_v2.life_liquidity.policy_state.os.replace", side_effect=OSError):
            with pytest.raises(OSError):
                journal.commit({"n": 1})
    with pytest.raises(ValueError):
        with journal.locked():
            pass

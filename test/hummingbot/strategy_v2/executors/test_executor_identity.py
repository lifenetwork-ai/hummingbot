"""Executor identity must survive same-tick proposal bursts and persisted replay."""
import random

import base58

from hummingbot.strategy_v2.executors.data_types import ExecutorConfigBase


def test_executor_identity_does_not_depend_on_a_small_random_nonce(monkeypatch):
    monkeypatch.setattr(random, 'randint', lambda *_: 7)
    configs = [ExecutorConfigBase(type='order_executor', timestamp=1000) for _ in range(256)]
    assert len({config.id for config in configs}) == len(configs)
    assert all(len(base58.b58decode(config.id)) == 32 for config in configs)
    for config in configs:
        restored = ExecutorConfigBase.model_validate(config.model_dump())
        assert restored.id == config.id and restored.timestamp == config.timestamp

"""No DB/driver/.env dependency for validating the explicitly selected PG2 config."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import AllocateBalance, PgConfig
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime_config import load_runtime_config


def configuration():
    return PgConfig('postgresql://runtime:private-test-password@127.0.0.1/db', 'runtime', uuid4(), uuid4(),
                    StateBinding('OFFLINE_SIMULATION', 'okx', '12345', 'usdt', uuid4(), 1, 'a' * 64, 'b' * 64),
                    16, 1, 2, 4, 1, 2, 1000, 1000, 3000, 4000, 2000, 2, 10, True)


@pytest.mark.parametrize('changes', [{'max_size': 0}, {'max_pending': 1}, {'max_size': True}, {'safety_size': 0},
                                     {'retry_count': 6}, {'server_major': 17}, {'transaction_timeout_ms': 60001},
                                     {'principal': 'unsafe; DROP ROLE x'}])
def test_unbounded_or_ambiguous_config_is_rejected(changes):
    with pytest.raises(StorageError):
        replace(configuration(), **changes)


def test_dsn_is_not_printed_and_loader_has_no_implicit_pool_bounds(tmp_path):
    cfg = configuration()
    assert 'private-test-password' not in repr(cfg)
    with pytest.raises(StorageError):
        load_runtime_config(binding=cfg.binding, account_id=cfg.account_id, database_lineage=cfg.database_lineage,
                            environ={}, no_env_file=True)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '1e38', '1e-19'])
def test_no_nonfinite_overflow_or_silent_decimal_rounding(value):
    with pytest.raises(StorageError):
        AllocateBalance('invalid', 0, 'USDT', Decimal(value))


def test_runtime_env_is_literal_and_environment_overrides_file(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime_config import INTEGERS
    cfg = configuration()
    file = tmp_path / 'runtime.env'
    values = {'LIFE_PG_' + key: str(getattr(cfg, field)) for key, field in INTEGERS.items()}
    values.update(LIFE_PG_DSN=cfg.dsn, LIFE_PG_PRINCIPAL=cfg.principal, LIFE_PG_ACCESS='runtime', LIFE_PG_LOCAL_INSECURE='true')
    file.write_text('\n'.join(key + '=' + value for key, value in values.items()))
    loaded = load_runtime_config(binding=cfg.binding, account_id=cfg.account_id, database_lineage=cfg.database_lineage,
                                 env_file=file, environ={'LIFE_PG_MAX_PENDING': '6'})
    assert loaded.max_pending == 6 and loaded.dsn == cfg.dsn

"""Retirement commands contain original proof IDs, never money or permissions."""
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_release import (
    PgSharedReleaseRepository,
    ReleaseSharedClaim,
)


def test_release_roundtrip_contains_only_original_operation_claim_and_terminal_identity():
    command = ReleaseSharedClaim('release1', 'swap1', 'terminal1')
    assert ReleaseSharedClaim.from_json(command.json()) == command


@pytest.mark.parametrize('data', [{}, {'operation_id': True, 'intent_id': 'swap', 'terminal_operation_id': 'terminal'},
                                  {'operation_id': 'release', 'intent_id': 'swap', 'terminal_operation_id': 'terminal', 'refund': '1'},
                                  {'operation_id': 'release', 'intent_id': '', 'terminal_operation_id': 'terminal'}])
def test_malformed_or_caller_money_fields_cannot_become_a_release(data):
    with pytest.raises(StorageError):
        ReleaseSharedClaim.from_json(data)


@pytest.mark.parametrize('field,value', [('released', False), ('released', 1), ('trading_permission', True),
                                         ('replayed', 0), ('kind', 'CAPITAL_RESERVE')])
def test_release_receipt_does_not_coerce_flags_or_grant_sends(field, value):
    repo = object.__new__(PgSharedReleaseRepository)
    repo._original = lambda: None  # Context fencing is native-tested at COMMIT.
    repo.binding = None
    receipt = dict(kind='SHARED_RELEASE', operation_id='release1', intent_id='swap1', released=True, trading_permission=False, replayed=False)
    parsed = repo._receipt(receipt)
    assert parsed.atomic_scope is AtomicScope.ACCOUNT and parsed.released and not parsed.trading_permission
    with pytest.raises(StorageError, match='PG_SHARED_RELEASE_RECEIPT_INVALID'):
        repo._receipt({**receipt, field: value})

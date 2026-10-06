"""An OKX risk account has one local LIFE recovery owner."""

import subprocess
import sys

import pytest

from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock


def test_same_uid_blocks_different_strategy_and_state_directories(tmp_path):
    first = AccountRiskPoolLock("12345", tmp_path)
    first.acquire()
    try:
        with pytest.raises(AccountLockUnavailable):
            AccountRiskPoolLock("12345", tmp_path).acquire()
        other_account = AccountRiskPoolLock("67890", tmp_path)
        other_account.acquire()
        other_account.release()
    finally:
        first.release()
    second = AccountRiskPoolLock("12345", tmp_path)
    second.acquire()
    second.release()


def test_other_process_cannot_acquire_owned_uid(tmp_path):
    owner = AccountRiskPoolLock("12345", tmp_path)
    owner.acquire()
    try:
        script = ("from pathlib import Path; "
                  "from hummingbot.strategy_v2.life_liquidity.account_lock import AccountRiskPoolLock; "
                  f"AccountRiskPoolLock('12345', Path({str(tmp_path)!r})).acquire()")
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        assert result.returncode != 0
        assert "ACCOUNT_LOCK_HELD_ELSEWHERE" in result.stderr
    finally:
        owner.release()


@pytest.mark.parametrize("uid", ["", "bad/uid", "123x", "-1"])
def test_invalid_uid_fails_closed(tmp_path, uid):
    with pytest.raises(ValueError, match="ACCOUNT_UID_INVALID"):
        AccountRiskPoolLock(uid, tmp_path)

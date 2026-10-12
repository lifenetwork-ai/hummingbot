"""Quote admission verifies joint recovery through its full controller gate.

Exit routes retain their explicit recovery check. The connector boundary always
re-evaluates permission; an earlier admission never becomes send authority.
"""
from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
from test.hummingbot.strategy_v2.life_liquidity.o8_history import (
    archive_financial_journals,
    archive_settled_history,
    seed_settled_history,
)
from test.hummingbot.strategy_v2.life_liquidity.test_executor_protected_send import _setup
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("archived", [False, True], ids=["legacy", "journals"])
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_quote_sender_uses_full_admission_without_an_extra_joint_check(tmp_path, monkeypatch, archived, history):
    async with system(tmp_path) as f:
        seed_settled_history(f, history)
        if archived:
            archive_settled_history(f)
            archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        action = f.c._quote_action_planner.propose()[0]
        sender = f.c._protected_spot_sender
        full_admission = f.c.allow_create_executor_actions
        joint_ready = f.c._joint_recovery_ready
        authorization = sender.gateway.authorize
        scope = {"authorization": False, "admission": False}
        checks = {"inside": 0, "outside": 0}

        def joint_check():
            if scope["authorization"]:
                checks["inside" if scope["admission"] else "outside"] += 1
            return joint_ready()

        def admission():
            scope["admission"] = True
            try:
                return full_admission()
            finally:
                scope["admission"] = False

        def authorize(permit):
            scope["authorization"] = True
            try:
                return authorization(permit)
            finally:
                scope["authorization"] = False

        monkeypatch.setattr(f.c, "_joint_recovery_ready", joint_check)
        monkeypatch.setattr(f.c, "allow_create_executor_actions", admission)
        monkeypatch.setattr(sender.gateway, "authorize", authorize)
        claim, wire = send(f, action)
        assert checks["inside"] >= 2  # Initial submit and connector send boundary.
        assert checks["outside"] == 0
        assert len(f.v[3].sent) == 1

        # Re-read the actual joint checkpoint at the connector boundary. This
        # also covers archived cuts and proves admission was not cached.
        path = f.joint.journal.path
        raw = path.read_bytes()
        changed = raw.replace(b'"phase":"COMMITTED"', b'"phase":"APPLYING"')
        assert changed != raw
        path.write_bytes(changed)
        with pytest.raises(PermissionError, match="SEND_PERMISSION_REVOKED"):
            f.v[3].sent[-1]["pre_send_check"](wire)
        assert f.v[1].retained_claim_ids() == (claim.intent_id,)


@pytest.mark.parametrize("inventory", [False, True], ids=["risk_reduction", "inventory"])
def test_exit_sender_retains_explicit_joint_denial_before_exit_admission(tmp_path, monkeypatch, inventory):
    controller, _, connector, _, _, _ = _setup(tmp_path)
    sender = controller._protected_spot_sender
    planner = SimpleNamespace()
    if inventory:
        monkeypatch.setattr(controller, "_inventory_executor", planner)
    monkeypatch.setattr(sender, "_exit_planner", lambda _: planner)
    checked = []

    def joint_denied():
        checked.append("joint")
        return False

    def forbidden_admission():
        pytest.fail("exit admission ran after joint recovery denial")

    monkeypatch.setattr(controller, "_joint_recovery_ready", joint_denied)
    monkeypatch.setattr(controller, "allow_risk_reduction_actions", forbidden_admission)
    monkeypatch.setattr(controller, "allow_inventory_execution_actions", forbidden_admission)
    assert sender._authorized(SimpleNamespace(intent_id="exit-1")) is False
    assert checked == ["joint"]
    assert connector.sent == []

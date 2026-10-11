"""O.7.7 reporting uses qualified joint facts and cannot substitute spot NAV."""
from dataclasses import replace
from decimal import Decimal
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import (
    install_attribution,
    report,
    send_close,
    setup_recovery,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.extension_telemetry import collect_extension_metrics

D = Decimal


@pytest.mark.asyncio
async def test_joint_report_separates_wallet_nav_margin_funding_and_actual_swap_fees(tmp_path):
    joint, v, bundle, _ = setup_recovery(tmp_path)
    install_attribution(tmp_path, v)
    assert await joint.reconcile()
    claim = send_close(v)
    report(v, bundle, claim)
    assert await joint.reconcile()
    metrics = collect_extension_metrics(v[0], synthetic=True)
    assert metrics["nav_quote"].value == D("19.999")
    assert metrics["net_pnl_quote"].value == D("-0.001")
    assert metrics["swap_long_base"].value == D("0.75")
    assert metrics["swap_short_base"].value == 0
    assert metrics["swap_fees_quote"].value == metrics["fees_quote"].value == D("0.001")
    assert metrics["funding_quote"].value == 0
    assert metrics["shared_collateral_quote"].value == D("100")
    assert metrics["nav_quote"].value != D("119.999")
    assert all(metric.quality == "synthetic" for metric in metrics.values() if metric.value is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stale", "missing", "sequence", "collector", "ownership", "no_joint", "no_attributor"])
async def test_unqualified_joint_report_has_null_values_not_spot_only_substitutes(tmp_path, fault):
    joint, v, bundle, _ = setup_recovery(tmp_path)
    install_attribution(tmp_path, v)
    assert await joint.reconcile()
    if fault == "no_joint":
        v[0]._joint_recovery = None
    elif fault == "no_attributor":
        v[0]._fill_attributor = None
    elif fault == "stale":
        v[6].advance(2)
    elif fault == "missing":
        joint.journal.path.unlink()
    elif fault == "sequence":
        v[8]["snapshot"] = replace(v[8]["snapshot"], sequence=2)
    elif fault == "ownership":
        v[0].config = v[0].config.model_copy(update={"recovery_state_dir": str(tmp_path)})
    else:
        joint._healthy = False
    metrics = collect_extension_metrics(v[0], synthetic=True)
    assert all(metric.value is None for metric in metrics.values())
    assert metrics["nav_quote"].quality == "missing"

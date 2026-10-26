import asyncio
from test.hummingbot.client.config.test_mm_profiles import CATALOGUE

import pytest

from hummingbot.client.config.mm_profiles import MMProfilesConfig


def catalogue():
    data = asyncio.run(MMProfilesConfig.load(CATALOGUE)).model_dump(mode="json")
    data["max_global_drawdown_quote"] = "20"
    data["spot_outflow"] = dict(window_seconds=60, max_net_outflow_quote="100", fee_allowance_rate=".01")
    for market in data["markets"].values():
        market["initial_anchor"] = ".1"
        market["quote_guards"] = dict(enabled=True, mode="bootstrap_then_market", max_deviation_bps="200",
                                      resume_deviation_bps="100", recovery_delay_seconds="1", bootstrap=dict(
                                          duration_seconds=30, max_anchor_move_bps="500", max_quote="50",
                                          min_external_depth_quote="100", max_market_spread_bps="250", qualification_seconds="2"))
    return data


@pytest.mark.parametrize("missing", ["anchor", "drawdown", "outflow", "duration", "budget", "window"])
def test_bootstrap_requires_complete_market_and_existing_risk_policies(missing):
    data = catalogue()
    market = data["markets"]["spot"]
    if missing == "anchor":
        market["initial_anchor"] = None
    elif missing == "drawdown":
        data["max_global_drawdown_quote"] = None
    elif missing == "outflow":
        data["spot_outflow"] = None
    elif missing == "duration":
        del market["quote_guards"]["bootstrap"]["duration_seconds"]
    elif missing == "budget":
        market["quote_guards"]["bootstrap"]["max_quote"] = "101"
    else:
        data["spot_outflow"]["window_seconds"] = 29
    with pytest.raises(ValueError):
        MMProfilesConfig.model_validate(data)


def test_catalogue_binds_stable_market_policies_without_an_extra_scenario_or_controller():
    result = MMProfilesConfig.model_validate(catalogue())
    policies = result.bootstrap_policies()
    assert set(policies) == {"life_spot", "life_perpetual"}
    assert policies["life_spot"]["initial_anchor"] == "0.1"
    assert len(result.controller_configs("sideways", "spot", None)) == 1
    assert set(result.profiles) == {"follow_market", "sideways", "follow_token"}

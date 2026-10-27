"""Economic replay must not manufacture fills, cash or a profitable baseline."""
from decimal import Decimal as D

import pytest


def test_touch_does_not_fill_and_queue_consumes_flow_once():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    orders = [PassiveOrder(str(i), "spot", "BUY", D(".1"), D("10"), 0, 1, D("5")) for i in range(2)]
    assert not match_flow(orders, now=0, aggressor="SELL", limit=D(".09"), quantity=D("100"), fraction=D(".5"))
    assert not match_flow(orders, now=1, aggressor="SELL", limit=D(".1"), quantity=D("100"), fraction=D(".5"))
    fills = match_flow(orders, now=2, aggressor="SELL", limit=D(".09"), quantity=D("12"), fraction=D(".5"))
    # A partial best-price fill cannot reuse leftover flow at a worse level.
    assert [(order.id, amount) for order, amount in fills] == [("0", D("5"))]
    assert orders[1].queue_ahead == D("5")


def test_price_priority_cancel_latency_and_no_fill_after_terminal():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    worse = PassiveOrder("worse", "spot", "BUY", D(".09"), D("10"), 0, 0, D("0"))
    better = PassiveOrder("better", "spot", "BUY", D(".1"), D("10"), 0, 0, D("0"))
    better.cancel_at = 3
    fills = match_flow([worse, better], now=2, aggressor="SELL", limit=D(".08"), quantity=D("2"), fraction=D("1"))
    assert [(order.id, amount) for order, amount in fills] == [("better", D("2"))]
    fills = match_flow([worse, better], now=3, aggressor="SELL", limit=D(".08"), quantity=D("2"), fraction=D("1"))
    assert [(order.id, amount) for order, amount in fills] == [("worse", D("2"))]


def test_expired_create_is_not_a_fill_but_expiry_does_not_erase_existing_quotes():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    late = PassiveOrder('late', 'spot', 'BUY', D('.1'), D('10'), 0, 4, D('0'))
    late.create_deadline = 3
    assert not match_flow([late], now=4, aggressor='SELL', limit=D('.09'), quantity=D('1'), fraction=D('1'))
    accepted = PassiveOrder('accepted', 'spot', 'BUY', D('.1'), D('10'), 0, 2, D('0'))
    accepted.create_deadline = 3
    accepted.cancel_at = 6
    assert match_flow([accepted], now=4, aggressor='SELL', limit=D('.09'), quantity=D('1'), fraction=D('1'))


def test_visible_better_liquidity_consumes_taker_flow_before_bot():
    from test.e2e.offline.mm_economics import PassiveOrder, match_flow
    order = PassiveOrder('1', 'spot', 'BUY', D('.099'), D('10'), 0, 1, D('5'))
    assert not match_flow([order], now=1, aggressor='SELL', limit=D('.09'), quantity=D('10'), fraction=D('1'),
                          external_levels=[(D('.0999'), D('20'))])
    fills = match_flow([order], now=2, aggressor='SELL', limit=D('.09'), quantity=D('30'), fraction=D('1'),
                       external_levels=[(D('.0999'), D('20'))])
    assert fills[0][1] == D('5')


def test_post_only_arrival_cannot_cross_known_own_orders():
    from test.e2e.offline.mm_economics import PassiveOrder, post_only_crosses
    old = PassiveOrder('1', 'spot', 'BUY', D('.1'), D('10'), 0, 1, D('0'))
    new = PassiveOrder('2', 'spot', 'SELL', D('.09'), D('10'), 1, 2, D('0'))
    assert post_only_crosses(new, [old], 2, None, None)
    assert not post_only_crosses(new, [], 2, None, None)


def test_exact_wallet_equity_hold_and_signed_funding():
    from test.e2e.offline.mm_economics import Wallet
    wallet = Wallet(cash=D("100"), base=D("1000"), initial_mark=D(".1"))
    wallet.fill("a", "spot", "BUY", D("2"), D(".1"), D(".01"))
    wallet.fill("b", "perpetual", "SELL", D("3"), D(".1"), D(".02"))
    assert wallet.cash == D("99.77") and wallet.base == D("1002")
    wallet.fund(D(".1"), D(".01"))  # Positive funding: short receives.
    assert wallet.funding == D(".003")
    assert wallet.equity(D(".08")) == D("179.993")
    assert wallet.equity(D(".08")) - wallet.hold_equity(D(".08")) == D("-.007")
    with pytest.raises(ValueError, match="duplicate"):
        wallet.fill("a", "spot", "BUY", D("2"), D(".1"), D(".01"))


def test_spot_no_short_and_markouts_are_side_signed_and_tail_censored():
    from test.e2e.offline.mm_economics import Wallet, markouts
    wallet = Wallet(cash=D("1"), base=D("0"), initial_mark=D(".1"))
    with pytest.raises(ValueError, match="balance"):
        wallet.fill("a", "spot", "SELL", D("1"), D(".1"), D("0"))
    fills = [dict(at=0, side="BUY", price=".1", amount="1"),
             dict(at=1, side="SELL", price=".1", amount="1")]
    result = markouts(fills, {0: D(".1"), 1: D(".09"), 2: D(".08")}, (1, 5))
    assert result["1"]["sum_quote"] == "0.01" and result["1"]["observed"] == 2
    assert result["5"]["observed"] == 0 and result["5"]["mean_quote"] is None
    assert result["5"]["censored"] == 2


@pytest.mark.asyncio
async def test_independent_exchange_weights_entry_prices_and_marks_short_with_correct_sign():
    from test.e2e.offline.exchange import Exchange
    exchange = Exchange(fixtures={"balances": {"data": [{"details": []}]},
                                  "order_event": {"arg": {}, "data": [{}]}})
    for index, price in enumerate((".1", ".2")):
        order = dict(instId="LIFE-USDT-SWAP", clOrdId=str(index), ordId=str(index),
                     side="sell", posSide="short", sz="100", px=price, accFillSz="0", state="live")
        exchange.orders[str(index)] = order
        await exchange.control({"fill": {"clOrdId": str(index), "amount": "100", "fee": "0"}})
    position = exchange.positions[("LIFE-USDT-SWAP", "short")]
    assert D(position["avgPx"]) == D(".15")
    assert D(position["upl"]) == D(".1")  # Two base short, mark .1.


def experiment():
    from pathlib import Path
    from test.e2e.offline.mm_economics_scenarios import load_experiment
    return load_experiment(Path('test/e2e/offline/economics.yaml'))


@pytest.mark.parametrize('change', [{'seeds': [True]}, {'initial_cash': 'NaN'}, {'profiles': ['sideways', 'sideways']},
                                    {'trend_reference': {'pair': 'SOL-USDT', 'weight': '1'}}])
def test_invalid_or_unmodelled_experiment_inputs_are_rejected(change):
    from test.e2e.offline.mm_economics_scenarios import Experiment

    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Experiment.model_validate({**experiment().model_dump(mode='json'), **change})


def test_exogenous_tape_is_seeded_and_independent_of_strategy():
    from test.e2e.offline.mm_economics_scenarios import scenario_tape, tape_hash
    first = scenario_tape('reference_divergence', 11, 60, D('.1'))
    assert first == scenario_tape('reference_divergence', 11, 60, D('.1'))
    assert tape_hash(first) != tape_hash(scenario_tape('reference_divergence', 29, 60, D('.1')))
    assert D(first[-1]['mark']) < D(first[0]['mark'])
    assert D(first[-1]['references']['AVAX-USDT']) > D(first[0]['references']['AVAX-USDT'])


@pytest.mark.asyncio
async def test_empty_replay_has_no_invented_profit_and_keeps_full_time_denominator():
    from test.e2e.offline.mm_economics_replay import run_case
    cfg = experiment().model_copy(update={'duration_seconds': 60})
    result = await run_case(cfg, scenario='empty_no_flow', seed=11, profile='sideways',
                            market_mode='spot', variant='fixed', sensitivity='assumed_base')
    assert result['ledger_verified'] and result['metrics']['fills'] == 0
    assert D(result['metrics']['account_pnl_quote']) == 0
    assert D(result['metrics']['excess_over_hold_after_cost_quote']) < 0
    liquidity = result['liquidity']['spot']
    assert liquidity['elapsed_seconds'] == 60 and liquidity['eligible_seconds'] == 0
    assert liquidity['eligible_coverage'] is None and liquidity['qualified_seconds'] < 60
    assert result == await run_case(cfg, scenario='empty_no_flow', seed=11, profile='sideways',
                                    market_mode='spot', variant='fixed', sensitivity='assumed_base')


@pytest.mark.asyncio
async def test_replay_uses_real_controller_and_validates_independent_exchange_ledger():
    from test.e2e.offline.mm_economics_replay import run_case
    cfg = experiment().model_copy(update={'duration_seconds': 60})
    result = await run_case(cfg, scenario='sideways', seed=11, profile='follow_market',
                            market_mode='both', variant='fee_volatility_inventory', sensitivity='assumed_base')
    assert result['ledger_verified'] and result['metrics']['fills'] > 0
    assert result['controller_class'] == 'controllers.market_making.life_mm.LifeMMController'
    assert D(result['metrics']['fees_quote']) > 0
    assert D(result['metrics']['peak_spot_outflow_with_reservations_quote']) <= cfg.outflow_cap_quote
    for values in result['markouts'].values():
        assert values['observed'] + values['censored'] == result['metrics']['fills']


@pytest.mark.asyncio
async def test_failed_matrix_retains_completed_case_without_claiming_acceptance(tmp_path):
    import json
    from test.e2e.offline.mm_economics_replay import run_case

    from scripts.test_mm_economics import run_experiment
    cfg = experiment().model_copy(update=dict(duration_seconds=60, seeds=[11], profiles=['sideways'],
                                              market_modes=['spot'], variants={'fixed': experiment().variants['fixed']},
                                              sensitivities={'assumed_base': experiment().sensitivities['assumed_base']}, scenarios=['empty_no_flow', 'sideways']))
    count = 0

    async def injected(config, **fields):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('deliberate later failure')
        return await run_case(config, **fields)

    output = tmp_path / 'partial'
    with pytest.raises(RuntimeError):
        await run_experiment(cfg, output, case_runner=injected)
    result = json.loads((output / 'result.json').read_text())
    assert not result['integrity_passed'] and result['completed_cases'] == 1
    assert (output / 'case-0001.json').exists() and not (output / 'review.md').exists()
    assert len((output / 'cases.jsonl').read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_rolling_sell_credit_expiry_is_reported_without_inventing_an_over_cap_buy():
    from test.e2e.offline.mm_economics_replay import run_case
    result = await run_case(experiment(), scenario='reference_divergence', seed=11, profile='sideways',
                            market_mode='spot', variant='fixed', sensitivity='assumed_base')
    assert D(result['metrics']['peak_spot_outflow_with_reservations_quote']) > experiment().outflow_cap_quote
    assert result['metrics']['outflow_above_cap_observations'] > 0
    admissions = result['raw']['spot_buy_admissions']
    assert admissions and all(D(row['committed_positive']) + D(row['reserved_after']) <= D(row['cap']) for row in admissions)

"""Offline, conditional economic replay. No exchange calls or profit forecast."""
import argparse
import asyncio
import hashlib
import itertools
import json
import platform
import statistics
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test.e2e.offline.mm_economics_replay import run_case  # noqa: E402
from test.e2e.offline.mm_economics_scenarios import load_experiment  # noqa: E402

SOURCES = ('controllers/market_making/life_mm.py', 'controllers/market_making/pmm_simple.py',
           'hummingbot/strategy_v2/controllers/market_making_controller_base.py',
           'hummingbot/strategy_v2/executors/position_executor/position_executor.py',
           'hummingbot/strategy_v2/backtesting/backtesting_engine_base.py',
           'hummingbot/strategy_v2/utils/mm_spread.py', 'hummingbot/strategy_v2/utils/inventory_skew.py',
           'hummingbot/strategy_v2/utils/listing_bootstrap.py', 'hummingbot/strategy_v2/utils/quote_guard.py',
           'hummingbot/core/spot_outflow.py', 'hummingbot/client/config/mm_profiles.py',
           'scripts/mm_load_ledger.py', 'scripts/test_mm_economics.py',
           'test/e2e/offline/exchange.py', 'test/e2e/offline/mm_economics.py',
           'test/e2e/offline/mm_economics_replay.py', 'test/e2e/offline/mm_economics_scenarios.py')


def source_hashes():
    values = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    catalogue = ROOT / 'deploy/cloudrun/conf/life_mm_profiles.yaml'
    if not catalogue.exists():
        catalogue = ROOT / 'conf/life_mm_profiles.yaml'
    values['deployed_catalogue'] = hashlib.sha256(catalogue.read_bytes()).hexdigest()
    return values


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')
    temporary.replace(path)


def aggregate(cases):
    groups = []
    keys = sorted({(row['market_mode'], row['variant'], row['sensitivity']) for row in cases})
    for mode, variant, sensitivity in keys:
        selected = [row for row in cases if (row['market_mode'], row['variant'], row['sensitivity']) == (mode, variant, sensitivity)]
        metrics = [row['metrics'] for row in selected]
        excess = [Decimal(row['excess_over_hold_after_cost_quote']) for row in metrics]
        quality = [Decimal(row['joint_quality_fraction']) for row in metrics]
        groups.append(dict(market_mode=mode, variant=variant, sensitivity=sensitivity, cases=len(selected),
                           median_excess_over_hold_quote=str(statistics.median(excess)), worst_excess_over_hold_quote=str(min(excess)),
                           best_excess_over_hold_quote=str(max(excess)), positive_excess_cases=sum(value > 0 for value in excess),
                           worst_equity_drawdown_quote=str(max(Decimal(row['max_equity_drawdown_quote']) for row in metrics)),
                           median_joint_quality_fraction=str(statistics.median(quality)), worst_joint_quality_fraction=str(min(quality)),
                           no_fill_cases=sum(row['fills'] == 0 for row in metrics),
                           outflow_above_cap_cases=sum(row['outflow_above_cap_observations'] > 0 for row in metrics),
                           negative_initial_margin_headroom_cases=sum(Decimal(row['minimum_initial_margin_headroom_quote']) < 0 for row in metrics),
                           total_fills=sum(row['fills'] for row in metrics), total_fees_quote=str(sum((Decimal(row['fees_quote']) for row in metrics), Decimal(0)))))
    return groups


def markdown(result):
    lines = ['# Pre-listing economic stress replay', '',
             '**Synthetic assumptions; not calibrated LIFE backtesting or a profit forecast.**', '',
             f"Completed {result['completed_cases']}/{result['expected_cases']} cases. Integrity passed: {result['integrity_passed']}. "
             f"Diagnostic subset: {result['diagnostic']}.", '',
             'Quotes come from the real LIFE controller and existing fee/NATR, inventory, guard and bootstrap helpers. '
             'Funding and position valuation reuse upstream holds and are checked against independent simulator fills/balances.', '',
             'Case pass means accounting/replay invariants, not profitability or production risk acceptance. '
             'All prices, fees, flows, queue/latency and funding events are explicit assumptions. '
             'The full elapsed-time denominator includes waiting, poor quotes and no-flow markets.', '',
             '| Mode | Variant | Assumptions | Cases | Median excess vs hold (USDT) | Worst excess | Worst equity DD | Median qualified time | No-fill cases |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for row in result['groups']:
        lines.append(f"| {row['market_mode']} | {row['variant']} | {row['sensitivity']} | {row['cases']} | "
                     f"{Decimal(row['median_excess_over_hold_quote']):.6f} | {Decimal(row['worst_excess_over_hold_quote']):.6f} | "
                     f"{Decimal(row['worst_equity_drawdown_quote']):.6f} | {Decimal(row['median_joint_quality_fraction']) * 100:.1f}% | {row['no_fill_cases']} |")
    lines += ['', 'Excess compares the final cash + marked spot inventory + perpetual UPL, including trading fees and synthetic funding, '
              'against holding the identical initial LIFE/USDT wallet, then subtracts illustrative operating cost. '
              'Drawdown is synthetic wallet equity peak-to-trough, not the production covered-V2 drawdown guard. '
              'Inventory remains open at scenario mark; there are no assumed liquidation proceeds or terminal exit fees.', '',
              'Markouts are side/quantity signed at 1/5/30 seconds; missing tail observations are censored, never zero-filled. '
              'A no-trade result has unknown fee/volume cost. Empty-market eligible coverage is null.', '',
              'Limits: external-only books; no calibrated real queue/impact; no maintenance margin/liquidation, '
              'transfers, private-feed lag, full PositionExecutor lifecycle, owner/DB/transport recovery or cloud validation. '
              'Inventory skew affects spot only; its perpetual-only variant is intentionally equivalent to fee/volatility.', '',
              'Inspect `result.json`, `cases.jsonl` and numbered case ledgers for individual scenarios. '
              'These aggregates mix stress regimes and do not rank an optimal trading strategy.', '']
    return '\n'.join(lines)


async def run_experiment(cfg, output, *, diagnostic=False, case_runner=run_case):
    output.mkdir(parents=True, exist_ok=False)
    frozen = source_hashes()
    result = dict(version=1, integrity_passed=False, diagnostic=diagnostic, expected_cases=cfg.case_count(), completed_cases=0,
                  assumptions=cfg.model_dump(mode='json'), source_sha256=frozen, python=platform.python_version(),
                  scope='Controller-level synthetic stress experiment; no market-calibrated returns, network or production risk acceptance.',
                  cases_file='cases.jsonl', groups=[], cases=[])
    save(output / 'result.json', result)
    cases, tapes = [], {}
    try:
        dimensions = itertools.product(cfg.scenarios, cfg.seeds, cfg.profiles, cfg.market_modes, cfg.variants, cfg.sensitivities)
        for index, (scenario, seed, profile, market_mode, variant, sensitivity) in enumerate(dimensions, 1):
            result['current_case'] = dict(index=index, scenario=scenario, seed=seed, profile=profile,
                                          market_mode=market_mode, variant=variant, sensitivity=sensitivity)
            case = await case_runner(cfg, scenario=scenario, seed=seed, profile=profile, market_mode=market_mode,
                                     variant=variant, sensitivity=sensitivity)
            key = (scenario, seed)
            if key in tapes and tapes[key] != case['tape_sha256']:
                raise AssertionError('Paired comparisons received different exogenous tapes')
            tapes[key] = case['tape_sha256']
            if not case['ledger_verified']:
                raise AssertionError('Unverified case ledger')
            tape_name = 'tape-' + case['tape_sha256'] + '.json'
            tape = case['raw'].pop('tape')
            if not (output / tape_name).exists():
                save(output / tape_name, tape)
            case['exogenous_tape_file'] = tape_name
            case['effective_controller_config_note'] = 'Catalogue defaults plus experiment overrides; source/config hashes retained.'
            save(output / f'case-{index:04d}.json', case)
            summary = {key: value for key, value in case.items() if key not in ('raw', 'effective_controllers')}
            summary['case_file'] = f'case-{index:04d}.json'
            cases.append(summary)
            with (output / 'cases.jsonl').open('a') as stream:
                stream.write(json.dumps(summary, separators=(',', ':'), allow_nan=False) + '\n')
            result['completed_cases'] = index
            save(output / 'progress.json', dict(completed_cases=index, expected_cases=cfg.case_count(), integrity_passed=False))
            if index % 18 == 0 or index == cfg.case_count():
                print(f'Economic replay: {index}/{cfg.case_count()} cases', file=sys.stderr, flush=True)
        if source_hashes() != frozen:
            raise AssertionError('Replay source changed during experiment')
        result.update(integrity_passed=True, cases=cases, groups=aggregate(cases),
                      exogenous_tape_count=len(tapes), completed_cases=len(cases))
        result.pop('current_case', None)
        (output / 'review.md').write_text(markdown(result))
    except BaseException as error:
        result.update(error_type=type(error).__name__, completed_cases=len(cases), cases=cases)
        raise
    finally:
        save(output / 'result.json', result)
        save(output / 'progress.json', dict(completed_cases=len(cases), expected_cases=cfg.case_count(),
                                            integrity_passed=result['integrity_passed']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'test/e2e/offline/economics.yaml')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--probe', action='store_true', help='Small diagnostic subset; not the full declared matrix.')
    parser.add_argument('--volatility-defense', action='store_true', help='Add an explicitly declared defense comparison variant; no profit or production calibration claim')
    args = parser.parse_args()
    cfg = load_experiment(args.config)
    if args.volatility_defense:
        from test.e2e.offline.mm_economics_scenarios import Variant
        cfg = cfg.model_copy(update={'variants': {**cfg.variants, 'volatility_defense': Variant(
            fee_floor=True, volatility=True, inventory_skew=True, defense=True)}})
        cfg = type(cfg).model_validate(cfg.model_dump())
    if args.probe:
        cfg = cfg.model_copy(update=dict(duration_seconds=60, seeds=cfg.seeds[:1],
                                         scenarios=[name for name in ('empty_no_flow', 'sideways') if name in cfg.scenarios]))
        if not cfg.scenarios:
            parser.error('Probe requires empty_no_flow or sideways in the declared experiment')
    output = args.output or ROOT / 'build/mm-economics' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    result = asyncio.run(run_experiment(cfg, output, diagnostic=args.probe))
    print(json.dumps(dict(integrity_passed=result['integrity_passed'], diagnostic=args.probe,
                          completed_cases=result['completed_cases'], output=str(output))))


if __name__ == '__main__':
    main()

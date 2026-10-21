"""PG2 real-process qualification in a new disposable local DB; no .env reads."""
import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations import IMAGE, docker
from uuid import uuid4


def main():
    import asyncpg  # noqa: F401 -- mandatory for this opt-in real-process runner.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', choices=('all', 'recorder_queries', 'recorder_proofs', 'codecs', 'commit_proofs', 'snapshot_proofs', 'service', 'service_trading', 'service_load', 'release_source', 'foundation', 'spot_producer', 'spot_allocation', 'spot_market_send', 'spot_market', 'joint_history', 'joint_intake', 'quote_composition', 'runner_startup', 'hedge_source', 'r2_integration', 'maker_cancellation', 'hedge_composition', 'r3_control', 'r3_risk', 'recorder', 'capital', 'session', 'lifecycle', 'actions', 'action_prepare', 'action_reconcile', 'action_abandon', 'runner_journal', 'provider_collection', 'quote_arm', 'quote_transport', 'quote_source', 'outbox', 'configuration', 'events', 'positions', 'reporting_application', 'approval_import', 'ack_ingress', 'observation_context', 'financial_ingress', 'financial_collection', 'inventory_settlement', 'markout', 'carry', 'swap', 'swap_accounting', 'swap_fills', 'funding', 'funding_repair', 'funding_cohort', 'swap_repair', 'spot_repair', 'cashflow_repair', 'terminal_repair', 'spot_recovery_paths', 'portfolio', 'swap_terminal', 'shared_release', 'joint_batch', 'joint_repair', 'hedge', 'swap_transport', 'collateral', 'joint_source', 'r1_accounting', 'joint_recovery', 'legacy_applying', 'legacy_joint', 'legacy_imports', 'late_capital'), default='all')
    parser.add_argument('--fail-fast', action='store_true', help='Stop at the first failure while retaining the original log/XML')
    parser.add_argument('--linux-image', help='Immutable locally built image ID; recorder suite only')
    args = parser.parse_args()
    if args.linux_image and (args.suite != 'recorder' or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.linux_image)):
        parser.error('--linux-image requires an immutable sha256 image ID and --suite recorder')
    output = args.output.resolve()
    if not output.is_relative_to(Path('/tmp').resolve()):
        parser.error('Use /tmp for disposable qualification logs')
    output.mkdir(parents=True, exist_ok=False)
    name, created = 'life-pg2-' + uuid4().hex[:12], False
    # Keep the local endpoint stable across the deliberate container crash.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        selected_port = probe.getsockname()[1]
    try:
        docker('run', '-d', '--name', name, '--label', 'life.pg2_test=disposable', '--cpus', '2', '--memory', '1g',
               '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-e', 'POSTGRES_DB=life_pg2_test', '-p', f'127.0.0.1:{selected_port}:5432', IMAGE)
        created = True
        for _ in range(100):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', '-d', 'life_pg2_test'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Disposable PostgreSQL not ready')
        docker('exec', name, 'psql', '-U', 'postgres', '-d', 'life_pg2_test', '-c', '''
            CREATE ROLE life_pg2_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_pg2_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
            CREATE ROLE life_pg2_reporter LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;''')
        (output / 'postgres_version.txt').write_text(docker('exec', name, 'psql', '-U', 'postgres', '-Atc', 'SELECT version()') + '\n')
        port = docker('port', name, '5432/tcp').split(':')[-1]
        env = {**os.environ, 'LIFE_PG_TEST_DSN': f'postgresql://postgres@127.0.0.1:{port}/life_pg2_test',
               'LIFE_PG_TEST_CONTAINER': name}
        selector = Path(__file__).with_name('postgres')
        if args.suite == 'recorder_queries':
            selector /= 'test_postgres_recorder_queries.py'
        elif args.suite == 'recorder_proofs':
            selector /= 'test_postgres_recorder_proofs.py'
        elif args.suite == 'codecs':
            selector /= 'test_postgres_json_codecs.py'
        elif args.suite == 'commit_proofs':
            selector /= 'test_postgres_commit_proofs.py'
        elif args.suite == 'snapshot_proofs':
            selector /= 'test_postgres_snapshot_financial_reuse.py'
        elif args.suite == 'recorder':
            selector /= 'test_postgres_recorder.py'
        elif args.suite == 'capital':
            selector /= 'test_postgres_capital.py'
        elif args.suite == 'session':
            selector /= 'test_postgres_session.py'
        elif args.suite == 'lifecycle':
            selector /= 'test_postgres_lifecycle.py'
        elif args.suite == 'actions':
            selector /= 'test_postgres_actions.py'
        elif args.suite == 'action_prepare':
            selector /= 'test_postgres_action_preparation.py'
        elif args.suite == 'action_reconcile':
            selector /= 'test_postgres_action_reconciliation.py'
        elif args.suite == 'action_abandon':
            selector /= 'test_postgres_action_abandonment.py'
        elif args.suite == 'runner_journal':
            selector /= 'test_postgres_runner_journal.py'
        elif args.suite == 'provider_collection':
            selector /= 'test_postgres_provider_collection.py'
        elif args.suite == 'quote_arm':
            selector /= 'test_postgres_quote_arm.py'
        elif args.suite == 'quote_transport':
            selector /= 'test_postgres_quote_transport.py'
        elif args.suite == 'markout':
            selector /= 'test_postgres_markout.py'
        elif args.suite == 'carry':
            selector /= 'test_postgres_carry.py'
        elif args.suite == 'swap':
            selector /= 'test_postgres_swap.py'
        elif args.suite == 'swap_accounting':
            selector /= 'test_postgres_swap_accounting.py'
        elif args.suite == 'swap_fills':
            selector /= 'test_postgres_swap_fills.py'
        elif args.suite == 'late_capital':
            selector /= 'test_postgres_late_capital.py'
        elif args.suite == 'hedge':
            selector /= 'test_postgres_hedge.py'
        elif args.suite == 'swap_transport':
            selector /= 'test_postgres_swap_transport.py'
        elif args.suite == 'collateral':
            selector /= 'test_postgres_collateral.py'
        elif args.suite == 'legacy_joint':
            selector /= 'test_postgres_legacy_joint.py'
        elif args.suite == 'legacy_applying':
            selector /= 'test_postgres_legacy_applying.py'
        elif args.suite == 'joint_repair':
            selector /= 'test_postgres_joint_repair.py'
        elif args.suite == 'joint_batch':
            selector /= 'test_postgres_joint_batch.py'
        elif args.suite == 'shared_release':
            selector /= 'test_postgres_shared_release.py'
        elif args.suite == 'swap_terminal':
            selector /= 'test_postgres_swap_terminal.py'
        elif args.suite == 'portfolio':
            selector /= 'test_postgres_portfolio.py'
        elif args.suite == 'terminal_repair':
            selector /= 'test_postgres_terminal_repair.py'
        elif args.suite == 'cashflow_repair':
            selector /= 'test_postgres_cashflow_repair.py'
        elif args.suite == 'spot_repair':
            selector /= 'test_postgres_spot_repair.py'
        elif args.suite == 'swap_repair':
            selector /= 'test_postgres_swap_repair.py'
        elif args.suite == 'funding_cohort':
            selector /= 'test_postgres_funding_cohort.py'
        elif args.suite == 'funding_repair':
            selector /= 'test_postgres_funding_repair.py'
        elif args.suite == 'funding':
            selector /= 'test_postgres_funding.py'
        elif args.suite == 'inventory_settlement':
            selector /= 'test_postgres_inventory_settlement.py'
        elif args.suite == 'financial_ingress':
            selector /= 'test_postgres_financial_ingress.py'
        elif args.suite == 'observation_context':
            selector /= 'test_postgres_observation_context.py'
        elif args.suite == 'ack_ingress':
            selector /= 'test_postgres_ack_ingress.py'
        elif args.suite == 'approval_import':
            selector /= 'test_postgres_approval_import.py'
        elif args.suite == 'reporting_application':
            selector /= 'test_postgres_recorder_application.py'
        elif args.suite == 'positions':
            selector /= 'test_postgres_positions.py'
        elif args.suite == 'events':
            selector /= 'test_postgres_events.py'
        elif args.suite == 'configuration':
            selector /= 'test_postgres_configuration.py'
        elif args.suite == 'outbox':
            selector /= 'test_postgres_outbox.py'
        elif args.suite == 'quote_source':
            selector /= 'test_postgres_quote_account_source.py'
        selectors = [str(selector)]
        if args.suite == 'service_load':
            selectors = [str(selector / ('test_' + name + '.py')) for name in ('flow_trace', 'postgres_service_load', 'postgres_provisional_service')]
        if args.suite == 'service_trading':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('service_trading', 'service_hedge', 'service_replay', 'service_process', 'service_swap_process', 'service_backend', 'service_spot_backend', 'service_fills', 'service_repair', 'quote_phase_boundaries')]
        if args.suite == 'runner_journal':
            selectors = [str(selector.parent / ('test_postgres_' + name + '.py')) for name in ('runner_journal', 'native_capture')]
        if args.suite == 'provider_collection':
            selectors = [str(selector.parent / ('test_postgres_' + name + '.py')) for name in (
                'provider_collection', 'provider_batch', 'provider_journal', 'financial_source', 'joint_account_source')]
        if args.suite == 'service':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'service', 'service_process', 'service_swap_process', 'service_backend', 'service_spot_backend', 'runner_startup', 'joint_events', 'joint_history', 'recorder_application', 'event_application')]
        if args.suite == 'release_source':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'release_proof_reuse', 'shared_release', 'swap_terminal', 'portfolio',
                'portfolio_proof_reuse', 'carry_proof_reuse', 'joint_source', 'collateral_source')]
        if args.suite == 'foundation':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'schema', 'transactions', 'pool_safety', 'faults', 'tls')]
            selectors.append(str(selector / 'test_zz_durability.py'))
        if args.suite == 'spot_producer':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('spot_producer', 'produced_send')]
        if args.suite == 'spot_allocation':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'spot_allocation', 'spot_market_send', 'hedge_source', 'hedge_application', 'capital', 'carry')]
        if args.suite == 'spot_market_send':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'service', 'service_trading', 'service_hedge', 'service_replay', 'service_process', 'service_swap_process', 'service_backend', 'service_spot_backend', 'service_fills', 'service_repair', 'service_cycle', 'standing_makers', 'retired_scope', 'maker_cancellation', 'operation_preflight', 'runner_startup', 'recorder_application', 'event_application',
                'spot_producer', 'produced_send', 'action_preparation', 'quote_arm', 'account_brackets', 'spot_allocation', 'spot_market_send', 'spot_market', 'spot_application', 'quote_transport', 'quote_account_source',
                'runner_journal', 'native_capture', 'command_fingerprint', 'release_proof_reuse', 'joint_source', 'hedge_source', 'hedge_application', 'joint_observer', 'financial_source', 'provider_batch', 'provider_collection', 'provider_journal', 'joint_account_source',
                'collateral_source', 'joint_history', 'joint_events',
                'snapshot_bodies', 'snapshot_financial_reuse', 'recorder_queries', 'recorder_proofs', 'json_codecs')]
            selectors.extend(str(selector / name) for name in ('test_shared_sdk_quota.py', 'test_spot_intent.py'))
        if args.suite == 'spot_market':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'spot_market', 'financial_source', 'provider_batch', 'provider_collection', 'hedge_source', 'joint_source', 'quote_account_source',
                'action_preparation', 'runner_journal')]
            selectors.append(str(selector / 'test_shared_sdk_quota.py'))
        if args.suite == 'joint_history':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'joint_events', 'joint_history', 'joint_observer', 'event_application', 'financial_dedup',
                'events', 'recorder_application', 'runner_startup', 'action_preparation', 'quote_arm')]
        if args.suite == 'joint_intake':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'joint_observer', 'joint_batch', 'funding', 'hedge_source', 'joint_source')]
        if args.suite == 'quote_composition':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'spot_application', 'runner_journal', 'action_preparation', 'action_reconciliation', 'action_abandonment',
                'quote_arm', 'quote_transport', 'quote_account_source', 'ack_ingress',
                'hedge_application', 'hedge', 'swap_transport', 'runner_startup', 'hedge_source', 'financial_source',
                'collateral_source', 'joint_source', 'maker_cancellation', 'provider_batch', 'provider_collection')]
            selectors.append(str(selector / 'test_shared_sdk_quota.py'))
        if args.suite == 'runner_startup':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'runner_startup', 'recorder_application', 'event_application', 'events', 'positions', 'configuration', 'outbox')]
        if args.suite == 'hedge_source':
            selectors = [str(selector / 'test_postgres_hedge_source.py')]
        if args.suite == 'r2_integration':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'hedge_source', 'provider_batch', 'provider_collection', 'financial_source', 'provider_journal', 'joint_account_source',
                'portfolio_proof_reuse', 'carry_proof_reuse', 'portfolio',
                'maker_cancellation', 'joint_source', 'lifecycle', 'hedge_application', 'hedge', 'swap_transport')]
        if args.suite == 'maker_cancellation':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('maker_cancellation', 'joint_source', 'lifecycle')]
        if args.suite == 'hedge_composition':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('hedge_application', 'hedge', 'swap_transport')]
        if args.suite == 'r3_risk':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('risk', 'risk_stress', 'risk_markout', 'markout', 'control', 'successor_control', 'execution_modes', 'inventory_exit', 'session', 'financial_commands', 'inventory_settlement', 'lifecycle')]
        if args.suite == 'r3_control':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('control', 'successor_control')]
        if args.suite == 'carry':
            selectors.append(str(selector.with_name('test_postgres_carry_proof_reuse.py')))
            selectors.append(str(selector.with_name('test_postgres_flow_proofs.py')))
        if args.suite == 'funding':
            selectors.append(str(selector.with_name('test_postgres_funding_history_reuse.py')))
            selectors.append(str(selector.with_name('test_postgres_money_commit_group.py')))
        if args.suite == 'legacy_imports':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in ('legacy_applying', 'legacy_core_cashflows', 'legacy_spot', 'legacy_joint', 'coupled_ack', 'file_recovery_history', 'completed_outer_history', 'outer_recovery_history', 'unfilled_swap', 'joint_trade_ids', 'received_observations')]
        if args.suite == 'joint_source':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'collateral_source', 'joint_source', 'joint_account_source', 'financial_source',
                'financial_observer', 'financial_dedup', 'provider_journal')]
        if args.suite == 'r1_accounting':
            # Fresh PostgreSQL accounting/recovery scope; active File cutover is
            # not selected. Keep complete native repair and release faults here.
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'transactions', 'lifecycle', 'inventory_settlement', 'collateral', 'collateral_source',
                'joint_source', 'joint_account_source', 'financial_source', 'financial_observer',
                'financial_dedup', 'provider_journal', 'command_fingerprint', 'joint_batch', 'joint_repair', 'swap_fills',
                'swap_accounting', 'funding', 'funding_history_reuse', 'flow_proofs', 'money_commit_group', 'funding_repair', 'funding_cohort', 'swap_repair',
                'spot_repair', 'cashflow_repair', 'terminal_repair', 'portfolio', 'portfolio_math',
                'swap_terminal', 'shared_release', 'late_capital')]
        if args.suite == 'joint_recovery':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'collateral', 'collateral_source', 'joint_source', 'joint_account_source', 'financial_source', 'financial_observer', 'financial_dedup', 'provider_journal', 'command_fingerprint', 'joint_batch', 'joint_repair', 'swap_fills', 'swap_accounting', 'funding', 'funding_repair', 'funding_cohort', 'swap_repair', 'spot_repair', 'cashflow_repair', 'terminal_repair', 'legacy_applying', 'legacy_core_cashflows', 'legacy_spot', 'legacy_joint', 'coupled_ack', 'file_recovery_history', 'completed_outer_history', 'outer_recovery_history', 'unfilled_swap', 'joint_trade_ids', 'received_observations', 'portfolio', 'swap_terminal', 'shared_release', 'late_capital')]
        if args.suite == 'spot_recovery_paths':
            selectors = [str(selector / ('test_postgres_' + name + '.py')) for name in (
                'spot_observations', 'received_observations', 'lifecycle', 'observation_context', 'inventory_settlement', 'financial_ingress', 'financial_commands')]
        command = [sys.executable, '-m', 'pytest', '-q', *selectors,
                   '--junitxml=' + str(output / 'integration.xml')]
        if args.suite == 'financial_collection':
            command.extend(['-k', 'financial_source or financial_observer'])
        with (output / 'integration.log').open('w') as log:
            if args.linux_image:
                root = Path(__file__).resolve().parents[5]
                manifest_paths = list((root / 'migrations/life_liquidity').glob('*.sql'))
                manifest_paths += [root / name for name in (
                    'hummingbot/model/sql_connection_manager.py', 'hummingbot/model/postgres_recorder.py',
                    'hummingbot/strategy_v2/life_liquidity/storage/postgres/spot.py',
                    'hummingbot/strategy_v2/life_liquidity/storage/postgres/runtime.py',
                    'controllers/generic/life_liquidity.py')]
                manifest = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in manifest_paths}
                docker_prefix = ['docker', 'run', '--rm', '--network', 'container:' + name,
                                 '--cpus', '2', '--memory', '2g',
                                 '-v', str(root / 'test') + ':/qualification/test:ro',
                                 '-v', str(output) + ':/results',
                                 '-e', 'PYTHONPATH=/home/hummingbot:/qualification',
                                 '-e', 'LIFE_PG_TEST_DSN=postgresql://postgres@127.0.0.1:5432/life_pg2_test',
                                 '--entrypoint', 'python', args.linux_image]
                smoke = subprocess.run(docker_prefix + ['-m',
                                                        'test.hummingbot.strategy_v2.life_liquidity.storage.linux_image_smoke', json.dumps(manifest)],
                                       stdout=log, stderr=subprocess.STDOUT)
                if smoke.returncode:
                    print('Linux image import/package smoke failed; see integration.log')
                    return smoke.returncode
                command = docker_prefix + ['-m', 'pytest', '-q',
                                           '/qualification/test/hummingbot/strategy_v2/life_liquidity/storage/postgres/test_postgres_recorder.py',
                                           '--junitxml=/results/integration.xml', '-o', 'cache_dir=/tmp/pytest-cache']
            if args.fail_fast:
                command.append('-x')
            result = subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        print((output / 'integration.log').read_text())
        return result.returncode
    finally:
        if created:
            (output / 'postgres.log').write_text(docker('logs', name))
            docker('rm', '-f', name)


if __name__ == '__main__':
    sys.exit(main())

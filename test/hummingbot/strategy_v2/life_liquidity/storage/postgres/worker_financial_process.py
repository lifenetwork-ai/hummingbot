"""Real foreground supervisor with native money and fake accepted HTTP work.

Only the connector/source fixture is injected. SIGTERM and SIGKILL are sent by
the independent parent after the original accepted send is durably unknown.
Never call this helper with a non-disposable database or real exchange client.
"""
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import encoded_config

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres import worker


async def launch(**kwargs):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

    import asyncpg

    from hummingbot.core.data_type.common import TradeType
    data = json.load(sys.stdin)
    assert data['migration_dsn'].startswith('postgresql://life_pg2_migrator@127.0.0.1:')
    assert data['root_dsn'].startswith('postgresql://postgres@127.0.0.1:')
    assert all('/life_pg2_' in data[key] for key in ('migration_dsn', 'root_dsn'))
    assert data['product'] in ('SPOT', 'SWAP')
    connection = await asyncpg.connect(data['migration_dsn'])
    options = {}
    if data['product'] == 'SWAP':
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import policy
        options['hedge_policy'] = policy()
    # Synthetic lifecycle headroom only. The original financial source ages,
    # SQL/COMMIT deadlines, risk, budget and SDK permission checks are intact.
    t, service = await configured((connection, data['root_dsn'], data['migration_dsn']), **options,
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    original = t.session.request

    async def accepted_without_reply(**request):
        response = await original(**request)
        if request['method'] == 'POST' and request['url'].endswith('/order'):
            manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                            sources=[asdict(r.source) for r in service.reporting.events], product=data['product'],
                            process_id=os.getpid(), account_config=t.config, balance=t.balance, positions=t.positions,
                            orders=t.service_orders, phase='send')
            # The test parent alone consumes this synthetic manifest. No DSN
            # or financial payload is added to the production worker logger.
            print(json.dumps(dict(event='FIXTURE_ACCEPTED_UNKNOWN', manifest=manifest)), flush=True)
            await asyncio.Event().wait()  # Physical HTTP response never arrives.
        return response
    t.session.request = accepted_without_reply
    await kwargs['on_started'](service)
    operation = (service.quote('whole-service-original', TradeType.BUY, 0)
                 if data['product'] == 'SPOT' else service.execute_hedge('whole-service-hedge'))
    original_task = asyncio.create_task(operation)
    stop_task = asyncio.create_task(kwargs['stop'].wait())
    done, _ = await asyncio.wait((original_task, stop_task), return_when=asyncio.FIRST_COMPLETED)
    if original_task in done:
        # An expired source/refused setup must fail the test; never print the
        # requested boundary or silently retry it with a new observation.
        await original_task
        raise AssertionError('financial boundary was not reached')
    assert service._revoked
    worker.event('FIXTURE_FINANCIAL_REVOKED', trading_permission=False)
    try:
        await service.close()  # Must retain/join the original blocked work.
    finally:
        # This must be unreachable before the foreground physical deadline;
        # exiting never certifies exchange terminality or releases money.
        worker.event('FIXTURE_FINANCIAL_CLOSE_RETURNED', trading_permission=False)
    await connection.close()


original_init = worker.WorkerSupervisor.__init__


def _reason(error):
    code = error.code if isinstance(error, StorageError) else None
    return code if type(code) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', code) else None


async def diagnostic_launch(**kwargs):
    try:
        return await launch(**kwargs)
    except Exception as error:
        # Test-only startup diagnostics: no exception message, DSN, traceback
        # locals or financial payload is sent to the parent. Do not retry.
        causes, seen, cause = [], {id(error)}, error.__cause__
        for _ in range(4):
            if cause is None or id(cause) in seen:
                break
            seen.add(id(cause))
            code = _reason(cause)
            if code is not None:
                causes.append(code)
            cause = cause.__cause__
        kind = ('StorageError' if isinstance(error, StorageError) else
                'TimeoutError' if isinstance(error, TimeoutError) else 'OtherError')
        print(json.dumps(dict(event='FIXTURE_SETUP_FAILED', error_kind=kind, reason_code=_reason(error), cause_codes=causes)), flush=True)
        raise


def install(self, config):
    original_init(self, config, launch=diagnostic_launch)


if __name__ == '__main__':
    worker.WorkerSupervisor.__init__ = install
    sys.exit(worker.main())

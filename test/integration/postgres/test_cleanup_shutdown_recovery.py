from test.integration.postgres.test_credential_disconnect import disconnect, loaded
from test.integration.postgres.test_credential_store import managed as managed_fixture

import pytest
from sqlalchemy import update

from hummingbot.model.bot_state import BotState
from hummingbot.model.order import Order

managed = managed_fixture


@pytest.mark.parametrize('blocker', [None, 'same_owner', 'unfrozen', 'pending_mutation', 'runtime_active',
                                     'orders_remaining', 'local_unresolved', 'no_previous_generation'])
def test_cleanup_fresh_proof_resolves_only_prior_owner_shutdown_fence(managed, blocker):
    store, actor = loaded(managed)
    root = disconnect(store, actor)
    state = store.store.read()
    operation = {**state['recovery_state']['credential_operation'], 'checkpoint_frozen': blocker != 'unfrozen',
                 'approved_generation': 2 if blocker == 'same_owner' else 1}
    if blocker == 'no_previous_generation':
        operation.pop('approved_generation')
    recovery = dict(credential_operation=operation, shutdown_incomplete=True,
                    runtime_active=blocker == 'runtime_active', halt=True, reason='unrelated risk HALT')
    if blocker == 'pending_mutation':
        recovery['pending_mutations'] = {'unverified': {}}
    with store.engine.begin() as connection:
        connection.execute(update(BotState).values(owner_uuid='replacement', owner_generation=2, recovery_state=recovery))
        if blocker == 'local_unresolved':
            connection.execute(Order.__table__.insert().values(id='unresolved', config_file_path='test.yml', strategy='test',
                                                               market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT',
                                                               creation_timestamp=1, order_type='LIMIT', amount=1, price=100,
                                                               last_status='OrderFailure', last_update_timestamp=1, creation_pending=True))
    proof = dict(orders_terminal_verified=True, account_verified=True, position_checkpoints_verified=True,
                 observed_at=float(store.store.read()['database_now']), owner_generation=2,
                 remaining_order_count=1 if blocker == 'orders_remaining' else 0, remaining_position_count=1)
    accepted = store.progress_disconnect(root['id'], 'replacement', 2, stage='closing_positions', evidence=proof)
    after = store.store.read()['recovery_state']
    assert accepted is (blocker is None)
    assert bool(after.get('shutdown_incomplete')) is (blocker is not None)
    assert after['halt'] and after['reason'] == 'unrelated risk HALT'
    assert store.store.read()['desired_state'] == 'stopped'

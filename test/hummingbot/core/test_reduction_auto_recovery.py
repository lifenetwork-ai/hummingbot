"""GET-only automatic recovery must preserve the exact managed closing effect."""
from copy import deepcopy
from test.hummingbot.cli.test_reduction_admission import closing_host

import pytest

from hummingbot.core.automatic_recovery import eligible


def closing_context():
    _, _, _, _, config, order = closing_host()
    return dict(executor=config.model_dump(mode='json'), session=['session', 0], order=order.to_json(),
                credential_fingerprint='a' * 64, cleanup=False)


def test_reviewed_managed_maker_close_is_eligible_for_get_only_recovery():
    assert eligible(closing_context())


@pytest.mark.parametrize('target,key,value', [
    ('executor', 'connector_name', 'okx'), ('executor', 'execution_strategy', 'MARKET'),
    ('executor', 'execution_strategy', 'LIMIT_CHASER'), ('executor', 'position_action', 'OPEN'),
    ('executor', 'mm_generation', True), ('executor', 'level_id', None), ('executor', 'id', None),
    ('executor', 'mm_session_id', None), ('executor', 'controller_id', ''),
    ('order', 'position', 'OPEN'), ('order', 'order_type', 'LIMIT'), ('order', 'trade_type', 'BUY'),
])
def test_unsupported_or_changed_closing_context_is_not_automatically_rearmed(target, key, value):
    context = closing_context()
    context[target][key] = value
    assert not eligible(context)


def test_cleanup_and_explicit_scope_mismatch_stay_outside_automatic_quote_recovery():
    context = closing_context()
    cleanup = deepcopy(context)
    cleanup['cleanup'] = True
    assert not eligible(cleanup)
    context['session'] = ['other-session', 0]
    assert not eligible(context)

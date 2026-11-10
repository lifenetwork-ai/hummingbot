"""Quote cancellation between ledger reads must not satisfy refresh settlement."""
from unittest.mock import Mock

from scripts import test_e2e_offline


def orders(buys, sells):
    return ([dict(clOrdId=x, side='buy') for x in buys]
            + [dict(clOrdId=x, side='sell') for x in sells])


def test_refresh_wait_uses_one_snapshot_during_old_sell_cancellation():
    old_ids = {'old-buy-1', 'old-buy-2', 'old-sell-1', 'old-sell-2'}
    old_sells = orders(['new-buy-1', 'new-buy-2'], ['old-sell-1', 'old-sell-2'])
    buys_only = orders(['new-buy-1', 'new-buy-2'], [])
    settled = orders(['new-buy-1', 'new-buy-2'], ['new-sell-1', 'new-sell-2'])
    # The old two-read predicate falsely passes old_sells -> buys_only.
    live = Mock(side_effect=[old_sells, buys_only, settled])
    assert not test_e2e_offline.refreshed_inventory_quotes(live, old_ids)
    assert live.call_count == 1
    assert not test_e2e_offline.refreshed_inventory_quotes(live, old_ids)
    assert live.call_count == 2
    assert test_e2e_offline.refreshed_inventory_quotes(live, old_ids) is settled
    assert live.call_count == 3


def test_refresh_wait_rejects_four_quotes_on_only_one_side():
    live = Mock(return_value=orders(['new-1', 'new-2', 'new-3', 'new-4'], []))
    assert not test_e2e_offline.refreshed_inventory_quotes(live, {'old'})
    live.assert_called_once_with()


def test_refresh_returns_the_validated_snapshot_without_an_extra_read():
    settled = orders(['new-buy-1', 'new-buy-2'], ['new-sell-1', 'new-sell-2'])
    live = Mock(side_effect=[settled, []])
    assert test_e2e_offline.refreshed_inventory_quotes(live, {'old'}) is settled
    live.assert_called_once_with()

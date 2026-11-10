"""A legacy test operator cannot waive identity, fills or original fee evidence."""
from copy import deepcopy
from decimal import Decimal

import pytest

from hummingbot.connector.exchange.okx.okx_utils import DEFAULT_FEES
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.trade_fee import TokenAmount, TradeFeeBase
from hummingbot.model.runtime_journal import digest
from scripts.mm_legacy_recovery_fixture import review


def proof():
    environment = dict(mode='demo', site='www')
    remote = dict(clOrdId='one', ordId='1', instId='BTC-USDT', side='buy', ordType='limit',
                  sz='.1', px='100', accFillSz='.04', state='partially_filled')
    fill = dict(clOrdId='one', ordId='1', instId='BTC-USDT', side='buy', tradeId='fill',
                fillSz='.04', fillPx='100', fee='-.01', feeCcy='USDT')
    original = dict(trading_pair='BTC-USDT', trade_type='BUY', order_type='LIMIT', amount='.1', price='100')
    order = dict(id='one', config_file_path='pmm.yml', market='okx', symbol='BTC-USDT', amount='.1', price='100',
                 exchange_order_id=None, creation_pending=True, attempt_context=dict(order=original, environment=environment,
                                                                                     credential_fingerprint=digest(['credential_identity', environment, 'okx', 'offline-key'])))
    fee = TradeFeeBase.new_spot_fee(DEFAULT_FEES, TradeType.BUY, percent_token='USDT',
                                    flat_fees=[TokenAmount('USDT', Decimal('.01'))])
    recorded = dict(order_id='one', exchange_trade_id='fill', market='okx', symbol='BTC-USDT',
                    trade_type='BUY', amount='.04', price='100', trade_fee=fee.to_json())
    return dict(orders=[remote], fills=[fill], unknown=[], positions=[]), [order], [recorded], environment


@pytest.mark.parametrize('missing', [True, False])
@pytest.mark.parametrize('maker', [True, False])
def test_review_only_authorizes_known_legacy_startup_and_never_claims_automatic_repair(missing, maker):
    ledger, orders, fills, env = proof()
    if maker:
        ledger['orders'][0]['ordType'] = 'post_only'
        orders[0]['attempt_context']['order']['order_type'] = 'LIMIT_MAKER'
    original = deepcopy(orders)
    result = review(ledger, orders, [] if missing else fills, env)
    assert not result['automatic'] and result['pending_intents'] == 1
    assert result['venue_fills'] == 1 and result['durable_fills'] == int(not missing)
    assert orders == original and orders[0]['creation_pending']


@pytest.mark.parametrize('fault', ['foreign_order', 'wrong_size', 'missing_venue_fill', 'fee_conflict', 'wrong_environment', 'wrong_key'])
def test_incomplete_or_conflicting_proof_cannot_authorize_legacy_initialization(fault):
    ledger, orders, fills, env = proof()
    if fault == 'foreign_order':
        ledger['orders'][0]['clOrdId'] = 'foreign'
    elif fault == 'wrong_size':
        orders[0]['amount'] = '.2'
    elif fault == 'missing_venue_fill':
        ledger['fills'] = []
    elif fault == 'fee_conflict':
        fills[0]['trade_fee']['flat_fees'][0]['amount'] = '.02'
    elif fault == 'wrong_environment':
        env = dict(mode='live', site='www')
    else:
        orders[0]['attempt_context']['credential_fingerprint'] = 'foreign'
    with pytest.raises(AssertionError):
        review(ledger, orders, fills, env)

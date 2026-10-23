"""Cold capital reconstruction uses original basis, never the closing wallet."""
import hashlib
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.test_handoff_authorization import LIMITS, setup
from test.hummingbot.strategy_v2.life_liquidity.storage.test_target_import import paid_history

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import pack_stopped_source, unpack_stopped_source
from hummingbot.strategy_v2.life_liquidity.storage.stopped_capital import project_stopped_capital


async def source_fixture(tmp_path, *, paid=True):
    service, _, _, _ = await setup(tmp_path, before_stage=paid_history if paid else None)
    try:
        source = await service.control.stopped_postgres_source(**LIMITS)
        expected = await service.store._call(service.store._capital_ledger,
                                             json.loads(source.base.core.checkpoint_bytes)['state']['resources']['commands'],
                                             json.loads(source.base.core.checkpoint_bytes)['state']['resources'].get('fills', {}), {})
        return source, expected
    finally:
        await service.close()


def recut(source, checkpoint, *, journals=None, archives=None):
    """Synthetic independently hashed corrupt/segmented input, not a grant."""
    journals = source.base.core.journal_images if journals is None else journals
    hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in journals}
    checkpoint['state']['resources']['journal_hashes'] = hashes
    cut = replace(source.base.core.cut,
                  checkpoint=replace(source.base.core.cut.checkpoint, state_digest=fingerprint(checkpoint['state'])),
                  components=tuple(sorted(hashes.items())))
    core = replace(source.base.core, checkpoint_bytes=encode(checkpoint), cut=cut, journal_images=journals)
    recorder = replace(source.base.recorder, cut=cut.checkpoint)
    return replace(source, base=replace(source.base, core=core, recorder=recorder),
                   archives=source.archives if archives is None else archives)


@pytest.mark.asyncio
@pytest.mark.parametrize('paid', [False, True])
async def test_cold_projection_retains_opening_basis_money_ids_and_observed_highwater(tmp_path, monkeypatch, paid):
    source, expected = await source_fixture(tmp_path, paid=paid)
    raw = pack_stopped_source(source, **LIMITS)
    cold = unpack_stopped_source(raw, expected_sha256=hashlib.sha256(raw).hexdigest(), **LIMITS)

    def no_source_io(*args, **kwargs):
        raise AssertionError('Cold projection must never open an embedded source path')
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', no_source_io)
        projection = project_stopped_capital(cold, max_rows=10000)
    body = projection.json()
    assert not projection.trading_permission
    assert D(body['balances']['LIFE']) == expected.life_balance
    assert D(body['balances']['USDT']) == expected.usdt_balance
    assert D(body['capital']['opening_inventory_remaining']) == expected.opening_inventory_remaining
    assert D(body['capital']['execution_loss_usdt']) == expected.execution_loss_quote
    assert body['accounting_policy']['opening_quote_usdt'] == '10'
    assert D(body['capital']['highwater_usdt']) >= expected.highwater_quote
    if paid:
        assert D(body['balances']['USDT']) == D('8.98')
        assert body['trade_ids'] == ['trade1']
        assert body['nav_history']['nav1'] == json.loads(source.base.core.checkpoint_bytes)['state']['resources']['nav_operations']['nav1']
    # Mutating a returned JSON object never changes the detached projection.
    body['balances']['USDT'] = '0'
    assert projection.json()['balances']['USDT'] != '0'


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['balance', 'missing_trade', 'liability', 'scope', 'capacity'])
async def test_projection_refuses_incomplete_reconciliation_and_capacity(tmp_path, fault):
    source, _ = await source_fixture(tmp_path)
    facts = source.base.core.facts
    if fault == 'balance':
        facts = replace(facts, account_quote_usdt=D('999'))
    elif fault == 'missing_trade':
        facts = replace(facts, account_trade_ids=())
    elif fault == 'liability':
        facts = replace(facts, liability_quote_usdt=D('1'))
    elif fault == 'scope':
        facts = replace(facts, order_scope_complete=False)
    source = replace(source, base=replace(source.base, core=replace(source.base.core, facts=facts)))
    with pytest.raises(StorageError):
        project_stopped_capital(source, max_rows=1 if fault == 'capacity' else 10000)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['highwater', 'nav_head', 'trade_identity', 'opening_policy', 'wallet', 'reference_after_cut', 'bill_anchor'])
async def test_rehashed_semantic_corruption_is_not_a_valid_capital_projection(tmp_path, fault):
    source, _ = await source_fixture(tmp_path)
    checkpoint = json.loads(source.base.core.checkpoint_bytes)
    resources = checkpoint['state']['resources']
    journals = dict(source.base.core.journal_images)
    if fault == 'highwater':
        resources['capital']['highwater_usdt'] = '1'
    elif fault == 'nav_head':
        resources['capital']['latest_operation_id'] = 'missing'
    elif fault == 'trade_identity':
        resources['fills']['trade1']['fill']['trade_id'] = 'different-trade'
    elif fault == 'opening_policy':
        checkpoint['policy']['legacy_spot']['accounting_policy']['opening_quote_usdt'] = '8.98'
    elif fault == 'reference_after_cut':
        resources['fills']['trade1']['fill']['independent_observed_at_ms'] = int(source.base.core.facts.observed_at.timestamp() * 1000) + 1
    elif fault == 'bill_anchor':
        anchor = checkpoint['policy']['legacy_spot']['accounting_policy']['anchor_bill_id']
        resources['additional_cashflow_approvals'] = {anchor: dict(bill_id=anchor, currency='USDT', signed_amount='1', transfer_at_ms=1)}
    else:
        wallet = json.loads(journals['reservations'])
        wallet['usdt_balance'] = '9'
        journals['reservations'] = encode(wallet)
    altered = recut(source, checkpoint, journals=tuple(sorted(journals.items())))
    with pytest.raises(StorageError):
        project_stopped_capital(altered, max_rows=10000)


@pytest.mark.asyncio
async def test_segmented_wallet_and_ordinary_wallet_have_identical_capital_projection(tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import capacity

    from hummingbot.strategy_v2.life_liquidity.journal_archive import JournalArchive
    from hummingbot.strategy_v2.life_liquidity.storage.archive_source import ArchiveSource
    source, _ = await source_fixture(tmp_path)
    expected = project_stopped_capital(source, max_rows=10000).json()
    journals = dict(source.base.core.journal_images)
    wallet = json.loads(journals['reservations'])
    archive = JournalArchive(tmp_path / 'synthetic-partition.json', capacity('risk'), kind='reservations', limits=wallet['limits'])
    root, segments = archive.archive.prepare(archive._split(wallet), archive.binding)
    evidence = ArchiveSource(archive.archive.policy, root, tuple(sorted(segments.items())))
    journals['reservations'] = root
    segmented = recut(source, json.loads(source.base.core.checkpoint_bytes), journals=tuple(sorted(journals.items())),
                      archives=(('core.reservations', evidence),))
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', lambda *args, **kwargs: pytest.fail('original paths opened'))
        actual = project_stopped_capital(segmented, max_rows=10000).json()
    assert actual.pop('source_digest') != expected.pop('source_digest')
    assert actual == expected
    detached = evidence.materialize()
    detached['state']['settled']['trades'].clear()
    assert evidence.materialize()['state']['settled']['trades']

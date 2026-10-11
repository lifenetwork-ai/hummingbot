"""Explicit synthetic journal partitions retain financial and identity history."""
import json
import os
from dataclasses import replace
from decimal import Decimal as D

import pytest

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

LIMITS = RiskLimits(D('0'), D('100'), D('100'), D('100'))


def capacity(kind, **changes):
    paths = (("settled",),) if kind == 'wal' else tuple(
        ('settled', key) for key in ('reservations', 'trades', 'account_events'))
    return replace(ArchivePolicy(paths=paths, records_per_segment=2, max_segment_bytes=8192,
                                 max_archive_bytes=262144, max_segments=64, max_active_bytes=16384), **changes)


def wal(tmp_path):
    book = IntentWAL(tmp_path / 'wal.json')
    book.initialize_empty()
    for i in range(4):
        key = str(i)
        book.prepare(key, client_order_id='wire-' + key, session_id='s', epoch=1, reservation_id=key)
        book.acknowledge(key, 'exchange-' + key)
        book.mark_terminal(key, 'exchange-' + key)
    book.prepare('pending', client_order_id='pending-wire', session_id='s', epoch=1, reservation_id='pending')
    return book


def reservations(tmp_path):
    book = ReservationLedger(life_balance=D('1'), usdt_balance=D('20'), limits=LIMITS,
                             path=tmp_path / 'risk.json')
    for i in range(4):
        key = str(i)
        intent = SpotIntent(key, 'BUY', D('1'), D('1'), 's', 1)
        assert book.reserve(intent, reference_price=D('1')).allowed
        book.record_fill(key, 'trade-' + key, D('0.5'), D('1'))
        book.record_fee('trade-' + key, 'USDT', D('-0.01'))
        book.confirm_terminal(key, cumulative_filled=D('0.5'), fills_reconciled=True, exchange_state='CANCELED')
    book.record_cashflow('101', 'USDT', D('2'))
    intent = SpotIntent('pending', 'BUY', D('2'), D('1'), 's', 1)
    assert book.reserve(intent, reference_price=D('1')).allowed
    book.record_fill('pending', 'pending-trade', D('0.5'), D('1'))
    book.record_fee('pending-trade', 'USDT', D('-0.01'))
    book.request_cancel('pending')
    return book


def cold(kind, book, policy=None):
    if kind == 'wal':
        return IntentWAL(book.path, archive=policy)
    return ReservationLedger.restore(book.path, limits=LIMITS, archive=policy)


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_partition_cold_restore_and_deduplication(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    manifest = json.loads(book.path.read_bytes())
    assert manifest['schema_version'] == 2
    active = manifest['state']['active']
    assert set(active if kind == 'wal' else active['reservations']) == {'pending'}
    assert b'trade-0' not in book.path.read_bytes()
    recovered = cold(kind, book, policy)
    if kind == 'wal':
        assert recovered.all_records() == book.all_records()
        assert recovered.scoped_order_ids('s', 1) == ('pending-wire',)
        assert recovered.find_by_client_order_id('wire-0').state == 'TERMINAL'
        with pytest.raises(ValueError, match='RECONCILE_BEFORE_RETRY'):
            recovered.begin('0', client_order_id='reused', session_id='s', epoch=1, reservation_id='0')
        with pytest.raises(ValueError, match='CLIENT_ORDER_ID_DUPLICATE'):
            recovered.begin('new', client_order_id='wire-0', session_id='s', epoch=1, reservation_id='new')
        recovered.acknowledge('pending', 'pending-exchange')
        recovered.mark_terminal('pending', 'pending-exchange')
        assert cold(kind, book, policy).scoped_order_ids('s', 1) == ()
    else:
        assert recovered.reservation_snapshot() == book.reservation_snapshot()
        assert recovered.reserved_usdt == D('1.5')
        assert (recovered.life_balance, recovered.usdt_balance) == (book.life_balance, book.usdt_balance)
        assert not recovered.record_fill('0', 'trade-0', D('0.5'), D('1'))
        assert not recovered.record_fee('trade-0', 'USDT', D('-0.01'))
        assert not recovered.record_cashflow('101', 'USDT', D('2'))
        recovered.verify_memory()
        recovered.confirm_terminal('pending', cumulative_filled=D('0.5'),
                                   fills_reconciled=True, exchange_state='CANCELED')
        assert cold(kind, book, policy).reserved_usdt == 0
    with pytest.raises(ValueError):
        cold(kind, book)
    with pytest.raises(ValueError):
        cold(kind, book, replace(policy, max_segments=65))


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_legacy_and_archived_stale_writers_cannot_erase_migration(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    legacy = cold(kind, book)
    policy = capacity(kind)
    book.migrate_archive(policy)
    stale = cold(kind, book, policy)
    if kind == 'wal':
        book.acknowledge('pending', 'pending-exchange')
    else:
        book.record_cashflow('102', 'USDT', D('1'))
    raw = book.path.read_bytes()
    for old in (legacy, stale):
        with pytest.raises(ValueError):
            if kind == 'wal':
                old.mark_cancel_requested('pending')
            else:
                old.record_cashflow('103', 'USDT', D('3'))
        assert book.path.read_bytes() == raw


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('fault', ['missing', 'same_stat_corruption', 'symlink'])
def test_history_integrity_is_checked_on_warm_and_cold_use(tmp_path, kind, fault):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    if kind == 'wal':
        book.assert_verified()
    else:
        book.verify_memory()
    manifest = json.loads(book.path.read_bytes())
    segment = manifest['archives'][0]['segments'][0]
    path = book.path.with_name(book.path.name + '.segments') / (segment['sha256'] + '.json')
    raw, stat = path.read_bytes(), path.stat()
    if fault == 'missing':
        path.unlink()
    elif fault == 'same_stat_corruption':
        path.write_bytes(raw.replace(b'BUY' if kind == 'risk' else b'wire-', b'BUX' if kind == 'risk' else b'wireX'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        copy = tmp_path / 'copy'
        copy.write_bytes(raw)
        path.unlink()
        path.symlink_to(copy)
    with pytest.raises(ValueError):
        book.assert_verified() if kind == 'wal' else book.verify_memory()
    with pytest.raises(ValueError):
        cold(kind, book, policy)


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_capacity_failure_does_not_publish_or_forget_pending_claim(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    raw = book.path.read_bytes()
    with pytest.raises(ValueError, match='ARCHIVE_CAPACITY'):
        book.migrate_archive(capacity(kind, max_archive_bytes=1))
    assert book.path.read_bytes() == raw
    cold(kind, book)
    # The bound is explicit; no live default or silent truncation is allowed.
    with pytest.raises(ValueError):
        book.migrate_archive(capacity('risk' if kind == 'wal' else 'wal'))


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('after_replace', [False, True])
def test_interrupted_migration_restores_old_or_complete_new_cut(tmp_path, kind, after_replace, monkeypatch):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    original = os.replace

    def fail(source, target):
        if target == book.path:
            if after_replace:
                original(source, target)
            raise OSError('synthetic publication interruption')
        return original(source, target)
    policy = capacity(kind)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', fail)
        with pytest.raises(OSError):
            book.migrate_archive(policy)
    raw = json.loads(book.path.read_bytes())
    recovered = cold(kind, book, policy if 'archive_policy' in raw else None)
    assert recovered.get('pending').state == 'SEND_UNKNOWN' if kind == 'wal' else recovered.reserved_usdt == D('1.5')
    with pytest.raises(ValueError):
        if kind == 'wal':
            book.mark_cancel_requested('pending')
        else:
            book.record_cashflow('102', 'USDT', D('1'))


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('fault', ['duplicate', 'wrong_partition'])
def test_manifest_cannot_duplicate_or_reclassify_settled_rows(tmp_path, kind, fault):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    manifest = json.loads(book.path.read_bytes())
    entry = manifest['archives'][0]
    segment = book.path.with_name(book.path.name + '.segments') / (entry['segments'][0]['sha256'] + '.json')
    row = json.loads(segment.read_bytes())['0']
    active = manifest['state']['active'] if kind == 'wal' else manifest['state']['active']['reservations']
    if fault == 'duplicate':
        active['0'] = row
    else:
        # A terminal row cannot masquerade as an active pending claim.
        active['pending'] = row
    book.path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        cold(kind, book, policy)


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_active_capacity_refusal_keeps_old_cut_and_all_history(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    raw = book.path.read_bytes()
    with pytest.raises(ValueError, match='ARCHIVE_CAPACITY'):
        key = 'oversized-' + 'x' * policy.max_active_bytes
        if kind == 'wal':
            book.begin(key, client_order_id=key, session_id='s', epoch=1, reservation_id=key)
        else:
            book.reserve(SpotIntent(key, 'BUY', D('1'), D('1'), 's', 1), reference_price=D('1'))
    assert book.path.read_bytes() == raw
    recovered = cold(kind, book, policy)
    assert recovered.get('pending') == book.get('pending') if kind == 'wal' else recovered.reserved_usdt == D('1.5')


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('cut', ['segment', 'before_manifest', 'after_manifest'])
def test_killed_migration_restores_complete_cut(tmp_path, kind, cut):
    import subprocess
    import sys

    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    script = '''
import os, sys
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import cold, capacity
path, kind, cut = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
class Holder:
    pass
holder = Holder()
holder.path = path
book = cold(kind, holder)
replace = os.replace
def crash(source, target):
    if target == path and cut == "before_manifest":
        os._exit(71)
    replace(source, target)
    if (target == path and cut == "after_manifest"
            or Path(target).parent.name.endswith(".segments") and cut == "segment"):
        os._exit(71)
os.replace = crash
book.migrate_archive(capacity(kind))
'''
    result = subprocess.run([sys.executable, '-c', script, str(book.path), kind, cut],
                            timeout=30, capture_output=True, text=True)
    assert result.returncode == 71, result.stderr
    schema = json.loads(book.path.read_bytes())
    assert ('archive_policy' in schema) == (cut == 'after_manifest')
    recovered = cold(kind, book, capacity(kind) if cut == 'after_manifest' else None)
    if kind == 'wal':
        assert recovered.all_records() == book.all_records()
    else:
        assert recovered.reservation_snapshot() == book.reservation_snapshot()
        assert recovered.cashflow_events == book.cashflow_events
        assert recovered.trade_ids == book.trade_ids


@pytest.mark.asyncio
@pytest.mark.parametrize('worker_enabled', [False, True])
async def test_archived_journals_through_real_protected_send_and_cancel(tmp_path, worker_enabled):
    from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import send, system
    from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history

    from hummingbot.strategy_v2.life_liquidity.history_worker import HistoryWorker, HistoryWorkerPolicy

    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        worker = HistoryWorker(HistoryWorkerPolicy(100, 100, 1024 * 1024, 1000)) if worker_enabled else None
        f.joint.history_worker = worker
        try:
            assert await f.joint.reconcile(), f.joint.reason_code
            planner = f.c._quote_action_planner
            planner.intent_id_factory = lambda: 'new-archived'
            actions = planner.propose()
            assert actions, planner.reason_code
            send(f, actions[0])
            ledger, journal = f.c._order_safety_reservations, f.c._order_safety_wal
            assert journal.get('new-archived').state == 'SEND_UNKNOWN'  # Harness sent; no exchange ACK yet.
            assert f.v[3].sent
            assert ledger.has_open_intent('new-archived')
            assert f.c._fill_attributor.ready()
            f.v[3].fail_status = True  # Cancel ACK alone cannot release a reservation.
            f.c._order_safety_manager.pause('SYNTHETIC_ARCHIVED_CANCEL')
            await f.c._cancel_and_reconcile_orders()
            assert journal.get('new-archived').cancel_requested
            pending = ledger.reservation_snapshot()['new-archived']
            assert pending.state in ('UNKNOWN', 'CANCEL_PENDING')
            assert pending.remaining_base == actions[0].executor_config.amount
            assert 'new-archived' in f.v[1].claim_ids()  # Cancel ACK cannot release capital.
            assert journal.reopen().all_records() == journal.all_records()
            assert ledger.reopen().reservation_snapshot() == ledger.reservation_snapshot()
        finally:
            if worker is not None:
                worker.close()


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_orphan_overflow_denies_new_risk_but_keeps_cancel_checkpoint(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    directory = book.path.with_name(book.path.name + '.segments')
    # Interrupted publication counts toward allocation; evidence is not pruned.
    orphan = directory / 'orphan.json'
    orphan.write_bytes(b'x' * (policy.max_segment_bytes + 1))
    before = book.path.read_bytes()
    with pytest.raises(ValueError, match='ARCHIVE_CAPACITY'):
        if kind == 'wal':
            book.begin('new', client_order_id='new-wire', session_id='s', epoch=1, reservation_id='new')
        else:
            book.reserve(SpotIntent('new', 'BUY', D('1'), D('1'), 's', 1), reference_price=D('1'))
    assert book.path.read_bytes() == before
    if kind == 'wal':
        book.mark_cancel_requested('pending')
        assert book.reopen().get('pending').cancel_requested
    else:
        book.mark_unknown('pending')
        assert book.reopen().requires_reconciliation('pending')
        assert book.reserved_usdt == D('1.5')
    assert orphan.is_file()


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_settlement_capacity_refusal_retains_pending_evidence(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    # No future-settlement headroom promise: this slice must fail closed when
    # already allocated segments exhaust the explicit capacity.
    policy = capacity(kind, max_segments=2 if kind == 'wal' else 7)
    book.migrate_archive(policy)
    raw = book.path.read_bytes()
    with pytest.raises(ValueError, match='ARCHIVE_CAPACITY'):
        if kind == 'wal':
            book.mark_terminal('pending', 'pending-exchange')
        else:
            book.confirm_terminal('pending', cumulative_filled=D('0.5'),
                                  fills_reconciled=True, exchange_state='CANCELED')
    assert book.path.read_bytes() == raw
    recovered = cold(kind, book, policy)
    if kind == 'wal':
        assert recovered.get('pending').state == 'SEND_UNKNOWN'
        recovered.mark_cancel_requested('pending')
    else:
        assert recovered.reserved_usdt == D('1.5')
        recovered.mark_unknown('pending')


def test_archive_restore_never_invents_missing_wal(tmp_path):
    with pytest.raises(ValueError, match='UNAVAILABLE'):
        IntentWAL(tmp_path / 'absent', archive=capacity('wal'))


@pytest.mark.asyncio
async def test_warm_permission_reuses_decoded_journals_but_checks_segment_bytes(tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import system
    from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history

    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        assert f.c.allow_create_executor_actions()
        wal, ledger = f.c._order_safety_wal, f.c._order_safety_reservations
        wal.verified_disk()
        ledger.verified_disk()

        def unexpected(*args, **kwargs):
            raise AssertionError('unchanged verified archive was decoded again')
        monkeypatch.setattr(IntentWAL, 'reopen', unexpected)
        monkeypatch.setattr(ReservationLedger, 'restore', unexpected)
        assert f.c.allow_create_executor_actions()
        manifest = json.loads(ledger.path.read_bytes())
        segment = manifest['archives'][0]['segments'][0]['sha256'] + '.json'
        path = ledger.path.with_name(ledger.path.name + '.segments') / segment
        raw, stat = path.read_bytes(), path.stat()
        path.write_bytes(raw.replace(b'BUY', b'BUX'))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert not f.c.allow_create_executor_actions()


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_warm_cache_cannot_disable_archive_binding(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    book.migrate_archive(capacity(kind))
    evidence = book.verified_disk()
    with pytest.raises(TypeError):
        evidence.path = tmp_path / 'unverified'
    book._archive = None
    with pytest.raises(ValueError):
        book.verified_disk()

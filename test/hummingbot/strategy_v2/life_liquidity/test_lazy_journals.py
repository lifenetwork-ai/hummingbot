"""Terminal evidence stays on disk; pending state and exact-byte fences remain."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.test_archived_journals import capacity, cold, reservations, wal

import pytest

from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import CheckpointArchive, encode
from hummingbot.strategy_v2.life_liquidity.lazy_archive import BLOOM_BITS, LazyArchiveMap, _bits
from hummingbot.strategy_v2.life_liquidity.risk import SpotIntent


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_cold_terminal_maps_are_lazy_and_reads_are_segment_bounded(tmp_path, kind, monkeypatch):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)

    def materialized(*args, **kwargs):
        raise AssertionError('cold journal restore materialized checkpoint history')
    monkeypatch.setattr(CheckpointArchive, 'read', materialized)
    recovered = cold(kind, book, policy)
    mapping = recovered._records if kind == 'wal' else recovered._reservations
    assert set(mapping.active) == {'pending'}
    assert mapping.settled.cached_records <= policy.records_per_segment
    assert len(mapping.settled) == 4
    assert recovered._archive.archive._image is None
    assert not hasattr(recovered._archive, '_logical_image')
    if kind == 'wal':
        assert recovered.get('0').state == 'TERMINAL'
        assert recovered.find_by_client_order_id('wire-3').intent_id == '3'
        assert recovered.scoped_order_ids('s', 1) == ('pending-wire',)
        with pytest.raises(ValueError, match='CLIENT_ORDER_ID_DUPLICATE'):
            recovered.begin('new', client_order_id='wire-3', session_id='s', epoch=1, reservation_id='new')
    else:
        assert recovered.trade_intent_id('trade-3') == '3'
        assert not recovered.record_fee('trade-3', 'USDT', D('-0.01'))
        assert recovered.preview().filled_base_total == D('2.5')
        assert recovered.reserved_usdt == D('1.5')
        assert not recovered.reserve(SpotIntent('0', 'BUY', D('1'), D('1'), 's', 1),
                                     reference_price=D('1')).allowed
    assert mapping.settled.cached_records <= policy.records_per_segment
    with pytest.raises(TypeError):
        mapping.active['invented'] = None
    with pytest.raises(TypeError):
        mapping.settled.entries = ()


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_active_queries_and_verified_memory_do_not_enumerate_terminal_maps(tmp_path, kind, monkeypatch):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    book.migrate_archive(capacity(kind))
    recovered = cold(kind, book, capacity(kind))
    recovered.assert_verified() if kind == 'wal' else recovered.verify_memory()
    mapping = recovered._records if kind == 'wal' else recovered._reservations

    def history_scan(*args, **kwargs):
        raise AssertionError('active permission query scanned terminal records')
    monkeypatch.setattr(type(mapping.settled), 'items', history_scan)
    if kind == 'wal':
        recovered.assert_verified()
        assert recovered.pending_reconciliation('s', 1) == ('pending-wire',)
        assert recovered.scoped_order_ids('s', 1) == ('pending-wire',)
    else:
        recovered.verify_memory()
        assert recovered.reserved_usdt == D('1.5')
        assert recovered.reserved_life == 0
        preview = recovered.preview()
        assert preview.filled_base_total == D('2.5')
        assert preview.pending_orders() == (('BUY', D('1.5'), D('1')),)
        assert not preview.check_and_hold(SpotIntent('3', 'BUY', D('1'), D('1'), 's', 1),
                                          reference_price=D('1')).allowed


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_lazy_cut_changes_remain_stale_and_pending_updates_do_not_retain_history(tmp_path, kind):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    recovered = cold(kind, book, policy)
    if kind == 'wal':
        recovered.mark_cancel_requested('pending')
    else:
        recovered.mark_unknown('pending')
    mapping = recovered._records if kind == 'wal' else recovered._reservations
    assert set(mapping.active) == {'pending'}
    assert mapping.settled.cached_records <= policy.records_per_segment
    with pytest.raises(ValueError):
        book.assert_verified() if kind == 'wal' else book.verify_memory()
    # A warm lazy cut is not permission after same-size terminal corruption.
    entry = json.loads(recovered.path.read_bytes())['archives'][0]['segments'][0]
    target = recovered.path.with_name(recovered.path.name + '.segments') / (entry['sha256'] + '.json')
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b'wire-' if kind == 'wal' else b'BUY', b'wireX' if kind == 'wal' else b'BUX'))
    with pytest.raises(ValueError):
        recovered.assert_verified() if kind == 'wal' else recovered.verify_memory()


def rewrite_segment(book, branch, edit):
    manifest = json.loads(book.path.read_bytes())
    entry = manifest['archives'][branch]['segments'][0]
    directory = book.path.with_name(book.path.name + '.segments')
    chunk = json.loads((directory / (entry['sha256'] + '.json')).read_bytes())
    edit(chunk)
    raw = encode(chunk)
    entry['sha256'], entry['bytes'] = hashlib.sha256(raw).hexdigest(), len(raw)
    (directory / (entry['sha256'] + '.json')).write_bytes(raw)
    book.path.write_bytes(encode(manifest))


@pytest.mark.parametrize('fault', ['wire_duplicate', 'wrong_fill_sum', 'fee_without_trade', 'overlapping_range', 'numeric_owner'])
def test_rehashed_invalid_history_still_fails_cold_financial_validation(tmp_path, fault):
    kind = 'wal' if fault in ('wire_duplicate', 'overlapping_range') else 'risk'
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    if fault == 'wire_duplicate':
        rewrite_segment(book, 0, lambda rows: rows['0'].update(client_order_id=rows['1']['client_order_id']))
    elif fault == 'wrong_fill_sum':
        rewrite_segment(book, 1, lambda rows: rows['trade-0'].__setitem__(1, '0.4'))
    elif fault == 'fee_without_trade':
        rewrite_segment(book, 2, lambda rows: rows.__setitem__('FEE:absent', rows.pop('FEE:trade-0')))
    elif fault == 'numeric_owner':
        rewrite_segment(book, 1, lambda rows: rows['trade-0'].__setitem__(0, 0))
    else:
        rewrite_segment(book, 0, lambda rows: rows.__setitem__('3', rows.pop('0')))
    with pytest.raises(ValueError):
        cold(kind, book, policy)


def test_bloom_collisions_never_omit_exact_wire_matches(tmp_path):
    # Repeated bit positions must be ORed, not added: addition can carry a
    # required membership bit away and would permit wire ID reuse.
    value = next('wire-' + str(i) for i in range(200000) if _bits('wire-' + str(i)).bit_count() < 4)
    book = wal(tmp_path)
    book.prepare('extra', client_order_id=value, session_id='s', epoch=1, reservation_id='extra')
    book.acknowledge('extra', 'extra-exchange')
    book.mark_terminal('extra', 'extra-exchange')
    book.migrate_archive(capacity('wal'))
    assert book.find_by_client_order_id(value).intent_id == 'extra'
    # Saturate the filter to force false positives; exact rows must still win.
    source = book._records.settled.source
    from dataclasses import replace
    saturated = LazyArchiveMap(source.directory,
                               [replace(entry, bloom=(1 << BLOOM_BITS) - 1) for entry in source.entries],
                               index_field='client_order_id')
    assert saturated.find(value)[0] == 'extra'
    with pytest.raises(KeyError):
        saturated.find('absent-wire')


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_cold_restore_rechecks_root_after_stream_validation(tmp_path, kind, monkeypatch):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    cls = type(book)
    method = '_install_archive_data' if kind == 'wal' else '_load_archive_data'
    original = getattr(cls, method)

    def raced(instance, data):
        original(instance, data)
        manifest = json.loads(book.path.read_bytes())
        manifest['state']['active'].clear()
        book.path.write_bytes(encode(manifest))
    monkeypatch.setattr(cls, method, raced)
    with pytest.raises(ValueError):
        cold(kind, book, policy)


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_pending_cancel_checkpoint_reuses_terminal_segments_without_row_scan(tmp_path, kind, monkeypatch):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    book.migrate_archive(capacity(kind))
    book.assert_verified() if kind == 'wal' else book.verify_memory()
    before = json.loads(book.path.read_bytes())['archives']
    mapping = book._records if kind == 'wal' else book._reservations

    def history_scan(*args, **kwargs):
        raise AssertionError('pending cancel checkpoint enumerated historical rows')
    monkeypatch.setattr(type(mapping.settled), 'items', history_scan)
    monkeypatch.setattr(type(book), 'reopen', history_scan)
    if kind == 'wal':
        book.mark_cancel_requested('pending')
        assert book.get('pending').cancel_requested
        book.mark_cancel_attempt('pending', at=datetime.now(timezone.utc))
    else:
        book.mark_unknown('pending')
        book.request_cancel('pending')
        assert book.requires_reconciliation('pending')
        assert book.reserved_usdt == D('1.5')
    assert json.loads(book.path.read_bytes())['archives'] == before


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('count', [0, 1000, 10000])
def test_retained_terminal_cache_bound_at_frozen_history_sizes(tmp_path, kind, count):
    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    data = json.loads(book.path.read_bytes())
    if kind == 'wal':
        template = data['records']['0']
        data['records'] = {'pending': data['records']['pending']}
        for i in range(count):
            key = f'history-{i:05}'
            data['records'][key] = {**template, 'intent_id': key, 'reservation_id': key, 'client_order_id': 'wire-' + key}
    else:
        template = data['reservations']['0']
        data['reservations'] = {'pending': data['reservations']['pending']}
        data['trades'] = {'pending-trade': data['trades']['pending-trade']}
        data['account_events'] = {'FEE:pending-trade': data['account_events']['FEE:pending-trade']}
        for i in range(count):
            key = f'history-{i:05}'
            data['reservations'][key] = {**template, 'intent': {**template['intent'], 'intent_id': key}}
            data['trades']['trade-' + key] = [key, '0.5', '1']
            data['account_events']['FEE:trade-' + key] = ['FEE', 'USDT', '-0.01']
    book.path.write_bytes(encode(data))
    book = cold(kind, book)
    # Explicit synthetic capacities, shared across all sizes, never live defaults.
    policy = capacity(kind, records_per_segment=64, max_segment_bytes=65536, max_active_bytes=262144,
                      max_segments=512, max_archive_bytes=16 * 1024 * 1024)
    book.migrate_archive(policy)
    recovered = cold(kind, book, policy)
    mapping = recovered._records if kind == 'wal' else recovered._reservations
    assert len(mapping.settled) == count
    assert set(mapping.active) == {'pending'}
    for key, _ in mapping.settled.items():
        assert mapping.settled.cached_records <= 64
    assert recovered._archive.archive._image is None
    if kind == 'wal':
        assert str(book.path.absolute()) not in book._verified_images
    else:
        assert book._verified_memory is None
        assert recovered.preview().filled_base_total == D(count) / 2 + D('0.5')


@pytest.mark.asyncio
async def test_attribution_cache_retains_lazy_views_and_rechecks_segment_integrity(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.o7_scenarios import system
    from test.hummingbot.strategy_v2.life_liquidity.o8_history import archive_financial_journals, seed_settled_history

    from hummingbot.strategy_v2.life_liquidity.lazy_archive import PartitionMap

    async with system(tmp_path) as f:
        seed_settled_history(f, 2)
        archive_financial_journals(f)
        assert await f.joint.reconcile(), f.joint.reason_code
        attribution = f.c._fill_attributor
        assert attribution.ready()
        memory = attribution._ready_cut[1]
        assert all(isinstance(memory[i], PartitionMap) for i in (3, 7, 8, 9))
        book = f.c._order_safety_reservations
        entry = json.loads(book.path.read_bytes())['archives'][0]['segments'][0]
        target = book.path.with_name(book.path.name + '.segments') / (entry['sha256'] + '.json')
        raw = target.read_bytes()
        target.write_bytes(raw.replace(b'BUY', b'BUX'))
        assert not attribution.ready()


@pytest.mark.parametrize('kind', ['wal', 'risk'])
@pytest.mark.parametrize('after_replace', [False, True])
def test_pending_checkpoint_ambiguous_replace_locks_writer(tmp_path, kind, after_replace, monkeypatch):
    import os

    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    policy = capacity(kind)
    book.migrate_archive(policy)
    original = os.replace

    def interrupted(source, target):
        if target == book.path:
            if after_replace:
                original(source, target)
            raise OSError('synthetic pending checkpoint interruption')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', interrupted)
        with pytest.raises(OSError):
            book.mark_cancel_requested('pending') if kind == 'wal' else book.mark_unknown('pending')
    with pytest.raises(ValueError):
        book.assert_verified() if kind == 'wal' else book.verify_memory()
    recovered = cold(kind, book, policy)
    if kind == 'wal':
        assert recovered.get('pending').cancel_requested == after_replace
        assert len(recovered.all_records()) == 5
    else:
        assert recovered.reservation_state('pending').state == ('UNKNOWN' if after_replace else 'CANCEL_PENDING')
        assert recovered.reserved_usdt == D('1.5')


@pytest.mark.parametrize('fault', ['count', 'digest', 'descriptor_bytes', 'branch', 'root_type', 'directory_symlink'])
def test_lazy_reader_rejects_invalid_manifest_metadata(tmp_path, fault):
    book = wal(tmp_path)
    policy = capacity('wal')
    book.migrate_archive(policy)
    manifest = json.loads(book.path.read_bytes())
    if fault == 'count':
        manifest['archives'][0]['count'] += 1
    elif fault == 'digest':
        manifest['archives'][0]['segments'][0]['sha256'] = '../escape'
    elif fault == 'descriptor_bytes':
        manifest['archives'][0]['segments'][0]['bytes'] = True
    elif fault == 'branch':
        manifest['archives'][0]['path'] = ['active']
    elif fault == 'root_type':
        manifest = []
    else:
        directory = book.path.with_name(book.path.name + '.segments')
        destination = tmp_path / 'moved'
        directory.rename(destination)
        directory.symlink_to(destination, target_is_directory=True)
    book.path.write_bytes(encode(manifest))
    with pytest.raises(ValueError):
        cold('wal', book, policy)


@pytest.mark.parametrize('kind', ['wal', 'risk'])
def test_repeated_snapshot_lookup_reuses_one_chunk_but_permission_rehashes_all_bytes(tmp_path, kind, monkeypatch):
    import hummingbot.strategy_v2.life_liquidity.lazy_archive as reader

    book = wal(tmp_path) if kind == 'wal' else reservations(tmp_path)
    book.migrate_archive(capacity(kind))
    lookup = (lambda: book.get('0')) if kind == 'wal' else (lambda: book.reservation_state('0'))
    expected = lookup()
    original, reads = reader.read_limited, []

    def counted(path, maximum):
        reads.append(path)
        return original(path, maximum)
    monkeypatch.setattr(reader, 'read_limited', counted)
    for _ in range(5):
        assert lookup() == expected
    assert reads == []  # Snapshot access cannot grant permission by itself.
    book.assert_verified() if kind == 'wal' else book.verify_memory()
    assert reads  # Permission still rehashes current segment bytes.
    entry = json.loads(book.path.read_bytes())['archives'][0]['segments'][0]
    target = book.path.with_name(book.path.name + '.segments') / (entry['sha256'] + '.json')
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b'wire-' if kind == 'wal' else b'BUY', b'wireX' if kind == 'wal' else b'BUX'))
    with pytest.raises(ValueError):
        book.assert_verified() if kind == 'wal' else book.verify_memory()


def test_wire_filter_does_not_thrash_the_single_chunk_cache_at_scale(tmp_path, monkeypatch):
    import hummingbot.strategy_v2.life_liquidity.lazy_archive as reader

    book = wal(tmp_path)
    data = json.loads(book.path.read_bytes())
    template = data['records']['0']
    data['records'] = {}
    for i in range(5000):
        key = f'history-{i:05}'
        data['records'][key] = {**template, 'intent_id': key, 'reservation_id': key, 'client_order_id': 'wire-' + key}
    book.path.write_bytes(encode(data))
    book = cold('wal', book)
    book.migrate_archive(capacity('wal', records_per_segment=500, max_segment_bytes=1024 * 1024,
                                  max_archive_bytes=16 * 1024 * 1024, max_active_bytes=262144))
    original, reads = reader.read_limited, []

    def counted(path, maximum):
        reads.append(path)
        return original(path, maximum)
    monkeypatch.setattr(reader, 'read_limited', counted)
    for i in range(0, 5000, 5):
        key = f'history-{i:05}'
        assert book.find_by_client_order_id('wire-' + key).intent_id == key
    # Explicit synthetic batch budget: ten segments, 1,000 point queries.
    # A saturated filter must not trigger hundreds of chunk decodes.
    assert len(reads) < 50
    assert book._records.settled.cached_records <= 500

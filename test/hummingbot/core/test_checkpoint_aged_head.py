"""A newly reviewed anchor can still refer to a fill outside recent history."""
from datetime import datetime, timezone
from test.hummingbot.core.test_segmented_recovery import Evidence, Venue, snapshot

import pytest

from hummingbot.core.checkpoint_recovery import verify_checkpoint_exchange
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.segmented_recovery import verify_segment

DAY = 86400000


class AgedHeadVenue(Venue):
    def __init__(self, outcome):
        super().__init__(2)
        self.outcome = outcome

    def fill(self, identity):
        stamp = DAY if identity == 1 else 7 * DAY - 1000
        return {**super().fill(identity), 'ts': str(stamp), 'fillTime': str(stamp)}

    async def _api_get(self, **kwargs):
        result = await super()._api_get(**kwargs)
        path = kwargs['path_url']
        if path.endswith('/fills') or self.outcome == 'missing_anchor':
            result['data'] = [r for r in result['data'] if r.get('billId') != '1']
        return result

    async def lookup(self, keys, watermark):
        if self.outcome == 'unrecorded_fill':
            return []
        # Any lookup of the old anchor itself would fabricate suffix coverage.
        assert all(trade == '2' for _, _, trade in keys)
        return await super().lookup(keys, watermark)


class QuietPerpetualVenue(AgedHeadVenue):
    name = 'okx_perpetual'

    async def _initialize_trading_pair_symbol_map(self):
        pass

    async def _api_get(self, **kwargs):
        result = await super()._api_get(**kwargs)
        if kwargs['path_url'].endswith('/config'):
            result['data'][0].update(posMode='long_short_mode', acctLv='2')
        return result


@pytest.mark.asyncio
@pytest.mark.parametrize('segmented', [False, True])
@pytest.mark.parametrize('suffix', [0, 1])
@pytest.mark.parametrize('outcome', ['complete', 'missing_anchor', 'unrecorded_fill'])
async def test_recent_review_does_not_make_six_day_old_head_recent(monkeypatch, segmented, suffix, outcome):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    venue, evidence, data = AgedHeadVenue(outcome), Evidence(), snapshot(suffix)
    venue.size = 1 + suffix
    data['state']['database_now'] = datetime.fromtimestamp(7 * DAY / 1000, timezone.utc)
    data['checkpoint']['anchor'].update(verified_at_ms=7 * DAY - 3600000, venue_heads={'okx': '1'})

    async def verify():
        if not segmented:
            return await verify_checkpoint_exchange(data, {'okx': venue}, venue.lookup)
        result = None
        while result is None:
            result = await verify_segment(data, {'okx': venue}, venue.lookup,
                                          evidence.progress, evidence.save, page_budget=1)
        return result

    if outcome == 'missing_anchor':
        with pytest.raises(RecoveryBlocked, match='recovery_exchange_scope_incomplete'):
            await verify()
    elif outcome == 'unrecorded_fill' and suffix:
        with pytest.raises(RecoveryBlocked, match='recovery_unrecorded_or_duplicate_venue_fill'):
            await verify()
    else:
        result = await verify()
        assert result['verified'] and result['fills_checked'] == suffix
        assert result['venue_heads'] == {'okx': str(1 + suffix)}
        assert result['venue_history_days'] == 89
        scans = [p for route, p in venue.calls if route.endswith('/fills-history') and 'end' in p]
        assert len(scans) == 2
        assert all('begin' not in p for p in scans)


@pytest.mark.asyncio
@pytest.mark.parametrize('segmented', [False, True])
async def test_active_spot_suffix_and_quiet_perpetual_prove_both_native_heads(monkeypatch, segmented):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    spot, perpetual, evidence, data = AgedHeadVenue('complete'), QuietPerpetualVenue('complete'), Evidence(), snapshot(1)
    perpetual.size = 1  # No recent perpetual fills; its only fill is the old anchor.
    data['state']['database_now'] = datetime.fromtimestamp(7 * DAY / 1000, timezone.utc)
    data['checkpoint']['anchor'].update(verified_at_ms=7 * DAY - 3600000,
                                        venue_heads={'okx': '1', 'okx_perpetual': '1'})
    data['bindings'].append({**data['bindings'][0], 'connector': 'okx_perpetual'})
    readers = {'okx': spot, 'okx_perpetual': perpetual}
    if segmented:
        result = None
        while result is None:
            result = await verify_segment(data, readers, spot.lookup, evidence.progress, evidence.save, page_budget=1)
    else:
        result = await verify_checkpoint_exchange(data, readers, spot.lookup)
    assert result['verified'] and result['fills_checked'] == 1
    assert result['venue_heads'] == {'okx': '2', 'okx_perpetual': '1'}
    assert len(evidence.identities) == (2 if segmented else 0)

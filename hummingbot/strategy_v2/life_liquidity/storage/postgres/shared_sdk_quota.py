"""Cold-start shared REST capacity for the two owned LIFE SDK instances.

Existing SDK limits still apply. A shared path budget additionally takes the
lowest count, longest interval and largest weight of both profiles. This is
conservative coordination within one process, never account/source authority,
cross-process exclusion or a claim about a live exchange's current quota.
"""
import math
from contextlib import asynccontextmanager
from decimal import Decimal

from hummingbot.connector.derivative.okx_perpetual import okx_perpetual_constants as SWAP
from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.api_throttler.data_types import RateLimit
from hummingbot.core.network_iterator import NetworkStatus
from hummingbot.core.web_assistant.web_assistants_factory import WebAssistantsFactory
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint


def _shape(throttler):
    def row(r):
        return (r.limit_id, str(r.limit), str(r.time_interval), str(r.weight),
                [(v.limit_id, str(v.weight)) for v in r.linked_limits])
    return fingerprint(dict(rows=[row(r) for r in throttler._rate_limits],
                            mapping=[(key, row(value)) for key, value in sorted(throttler._id_to_limit_map.items())],
                            share=str(throttler.limits_pct), margin=str(throttler._safety_margin_pct),
                            retry=str(throttler._retry_interval), lock=id(throttler._lock), logs=id(throttler._task_logs)))


def _route(limit_id):
    value = limit_id
    for prefix in ('GET-', 'POST-', 'DELETE-', 'PUT-'):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.endswith('-LIFE-USDT'):
        value = value[:-len('-LIFE-USDT')]
    return value if value.startswith('/api/v5/') else None


class _QuotaView:
    def __init__(self, owner, base):
        self._owner, self._base = owner, base
        self._base_execute = base.execute_task
        self._execute_hook, self._add_hook, self._set_hook = self.execute_task, self.add_rate_limits, self.set_rate_limits

    @property
    def limits_pct(self):
        return self._base.limits_pct

    def get_related_limits(self, limit_id):
        self._owner.assert_binding()
        if limit_id not in self._base._id_to_limit_map:
            raise StorageError('PG_SHARED_QUOTA_ROUTE_UNQUALIFIED')
        return self._base.get_related_limits(limit_id)

    def add_rate_limits(self, rows):
        self._owner.assert_binding()
        # The fixed LIFE SDK's normal start_network repeats already registered
        # pair limits. New pairs/profiles require a separate cold qualification;
        # never recreate a budget and silently forget consumed capacity.
        for row in rows:
            old = self._base._id_to_limit_map.get(row.limit_id)
            limit = max(Decimal('1'), math.floor(Decimal(str(row.limit)) * self._base.limits_pct))
            if (old is None or (limit, row.time_interval, row.weight, row.linked_limits) !=
                    (old.limit, old.time_interval, old.weight, old.linked_limits)):
                self._owner.revoke()
                raise StorageError('PG_SHARED_QUOTA_PROFILE_CHANGED')

    def set_rate_limits(self, rows):
        self._owner.revoke()
        raise StorageError('PG_SHARED_QUOTA_PROFILE_CHANGED')

    def execute_task(self, limit_id):
        self.get_related_limits(limit_id)
        return self._slot(limit_id)

    @asynccontextmanager
    async def _slot(self, limit_id):
        owner = self._owner
        owner.assert_binding()
        async with self._base_execute(limit_id):
            owner.assert_binding()
            route = _route(limit_id)
            if route is None:
                yield  # Existing SDK WebSocket limits retain their own scope.
            else:
                async with owner._shared.execute_task(route):
                    # Credentials/factory/profile changes during either quota
                    # wait revoke both SDKs before REST auth or HTTP is entered.
                    owner.assert_binding()
                    yield


class PgSharedSdkQuota:
    @staticmethod
    def _sources_match(sdk, factory):
        if sdk.order_book_tracker.data_source._api_factory is not factory:
            return False
        tracker = sdk._user_stream_tracker
        return tracker is None or tracker.data_source._api_factory is factory

    @staticmethod
    def _cold(sdk):
        tracker = sdk.order_book_tracker
        user = sdk._user_stream_tracker
        return (sdk.network_status is NetworkStatus.STOPPED and not tracker._tracking_tasks
                and (user is None or user._user_stream_tracking_task is None and user.data_source._ws_assistant is None)
                and all(getattr(tracker, name, None) is None for name in (
                    '_emit_trade_event_task', '_init_order_books_task', '_order_book_diff_listener_task',
                    '_order_book_trade_listener_task', '_order_book_snapshot_listener_task',
                    '_order_book_diff_router_task', '_order_book_snapshot_router_task',
                    '_update_last_trade_prices_task', '_order_book_stream_listener_task'))
                and all(getattr(sdk, name, None) is None for name in (
                    '_status_polling_task', '_user_stream_tracker_task', '_trading_rules_polling_task',
                    '_trading_fees_polling_task', '_user_stream_event_listener_task',
                    '_funding_fee_polling_task', '_funding_info_listener_task', '_lost_orders_update_task')))

    @classmethod
    def for_connector(cls, connector):
        try:
            if type(connector) not in (OkxExchange, OkxPerpetualDerivative):
                raise ValueError
            view = getattr(connector._throttler, '_life_shared_quota', None)
            if view is None:
                return None
            if type(view) is not _QuotaView:
                raise ValueError
            owner = view._owner
            if type(owner) is not cls or not any(row[0] is connector for row in owner._records):
                raise ValueError
            owner.assert_binding()
            return owner
        except (AttributeError, TypeError, ValueError, StorageError):
            raise StorageError('PG_SHARED_QUOTA_CONTEXT_CHANGED') from None

    @staticmethod
    def _values(sdk):
        if type(sdk) is OkxExchange:
            return (sdk.okx_api_key, sdk.okx_secret_key, sdk.okx_passphrase)
        return (sdk.okx_perpetual_api_key, sdk.okx_perpetual_secret_key, sdk.okx_perpetual_passphrase)

    @staticmethod
    def _auth_values(sdk):
        a = sdk._auth
        return (a.api_key, a.secret_key, a.passphrase) if type(sdk) is OkxExchange else (a._api_key, a._api_secret, a._passphrase)

    @classmethod
    def install(cls, *, spot, swap):
        try:
            if (type(spot) is not OkxExchange or type(swap) is not OkxPerpetualDerivative
                    or spot._throttler is swap._throttler
                    or spot.domain.rstrip('/') != SWAP.REST_URLS[swap.domain].rstrip('/')
                    or cls._values(spot) != cls._values(swap)
                    or any(type(v) is not str or not v for v in cls._values(spot))):
                raise ValueError
            records, groups = [], {}
            for sdk in (spot, swap):
                factory, base, auth = sdk._web_assistants_factory, sdk._throttler, sdk._auth
                if (type(factory) is not WebAssistantsFactory or type(base) is not AsyncThrottler
                        or factory.throttler is not base or factory.auth is not auth
                        or getattr(base, '_life_shared_quota', None) is not None
                        or cls._auth_values(sdk) != cls._values(sdk)
                        or tuple(sdk.trading_pairs) != ('LIFE-USDT',) or base._task_logs
                        or sdk._order_tracker.active_orders or not cls._cold(sdk)
                        or not cls._sources_match(sdk, factory)):
                    raise ValueError
                records.append((sdk, factory, base, auth, sdk.domain, cls._values(sdk), _shape(base)))
                for row in base._id_to_limit_map.values():
                    route = _route(row.limit_id)
                    if route is not None:
                        if (not all(Decimal(str(v)).is_finite() for v in (row.limit, row.weight, row.time_interval))
                                or row.limit < row.weight or row.weight <= 0 or row.time_interval <= 0):
                            raise ValueError
                        group = groups.setdefault(route, [row.limit, row.time_interval, row.weight])
                        group[:] = min(group[0], row.limit), max(group[1], row.time_interval), max(group[2], row.weight)
            if any(limit < weight for limit, interval, weight in groups.values()):
                raise ValueError
            quota = cls()
            quota._records, quota._revoked = tuple(records), False
            quota._shared = AsyncThrottler([RateLimit(path, limit, interval, weight)
                                           for path, (limit, interval, weight) in sorted(groups.items())])
            quota._shared_shape = _shape(quota._shared)
            quota._views = tuple(_QuotaView(quota, row[2]) for row in records)
        except (AttributeError, KeyError, TypeError, ValueError, StorageError):
            raise StorageError('PG_SHARED_QUOTA_BOOTSTRAP_INVALID') from None
        # Complete validation precedes all changes; no awaits or partial
        # installation when the second SDK fails validation.
        for (_, _, base, *_), view in zip(quota._records, quota._views):
            # Keep the original object: SDK time-provider closures and cached
            # REST assistants also hold it. Replacing only factory.throttler
            # would leave their auxiliary HTTP outside the shared budget.
            base._life_shared_quota = view
            base.execute_task = view._execute_hook
            base.add_rate_limits = view._add_hook
            base.set_rate_limits = view._set_hook
        return quota

    def assert_binding(self):
        try:
            if self._revoked or _shape(self._shared) != self._shared_shape:
                raise ValueError
            for (sdk, factory, base, auth, domain, values, shape), view in zip(self._records, self._views):
                if (sdk._throttler is not base or sdk._web_assistants_factory is not factory
                        or factory.throttler is not base or factory.auth is not auth or sdk._auth is not auth
                        or getattr(base, '_life_shared_quota', None) is not view
                        or base.execute_task is not view._execute_hook
                        or base.add_rate_limits is not view._add_hook or base.set_rate_limits is not view._set_hook
                        or view._owner is not self or view._base is not base or sdk.domain != domain
                        or self._values(sdk) != values or self._auth_values(sdk) != values
                        or not self._sources_match(sdk, factory)
                        or tuple(sdk.trading_pairs) != ('LIFE-USDT',) or _shape(base) != shape):
                    raise ValueError
        except (AttributeError, KeyError, TypeError, ValueError, StorageError):
            self.revoke()
            raise StorageError('PG_SHARED_QUOTA_CONTEXT_CHANGED') from None

    def revoke(self):
        self._revoked = True

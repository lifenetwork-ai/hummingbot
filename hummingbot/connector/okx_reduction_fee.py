"""Opt-in account fee observations, independent of configured fee estimates.

Only the background reader performs I/O. Quote preparation and both send gates
consume the same bounded immutable cache, with monotonic expiry.
"""
import math
import time
from dataclasses import dataclass
from decimal import Decimal

from hummingbot.strategy_v2.utils.perpetual_reduction import ClosingFeeBound

FEE_MAX_AGE_SECONDS = 30
FEE_POLL_SECONDS = 10


def _clock(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('reduction_fee_unavailable')


@dataclass(frozen=True)
class FeeObservation:
    bound: ClosingFeeBound
    observed_monotonic: float

    def __post_init__(self):
        _clock(self.observed_monotonic)
        if not isinstance(self.bound, ClosingFeeBound):
            raise ValueError('reduction_fee_unavailable')

    def current(self, monotonic_now):
        _clock(monotonic_now)
        if not 0 <= monotonic_now - self.observed_monotonic <= FEE_MAX_AGE_SECONDS:
            raise ValueError('reduction_fee_unavailable')
        return self.bound

    @classmethod
    def decode(cls, response, *, wall_now, monotonic_now):
        _clock(wall_now)
        _clock(monotonic_now)
        try:
            if (not isinstance(response, dict) or response.get('code') != '0'
                    or not isinstance(response.get('data'), list) or len(response['data']) != 1):
                raise ValueError
            row = response['data'][0]
            if not isinstance(row, dict) or row.get('instType') != 'SWAP':
                raise ValueError
            stamp = row.get('ts')
            if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdigit() or len(stamp) > 17:
                raise ValueError
            if not -1 <= wall_now - int(stamp) / 1000 <= FEE_MAX_AGE_SECONDS:
                raise ValueError
            if 'feeGroup' in row:
                groups = row['feeGroup']
                if (not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict)
                        or not isinstance(groups[0].get('groupId'), str) or not groups[0]['groupId'].isdigit()):
                    raise ValueError
                raw = groups[0].get('maker')
            else:
                # Explicit compatibility for the documented USDT-margined field.
                # Never use the deprecated crypto-margined `maker` as its substitute.
                raw = row.get('makerU')
            if not isinstance(raw, str) or not raw or len(raw) > 256:
                raise ValueError
            rate = Decimal(raw)
            if not rate.is_finite() or abs(rate) >= 1:
                raise ValueError
            return cls(ClosingFeeBound(max(Decimal(0), -rate), Decimal(0)), monotonic_now)
        except (ValueError, ArithmeticError, TypeError, KeyError):
            raise ValueError('reduction_fee_unavailable') from None


async def read_reduction_fee(connector, pair, *, wall_now=None, monotonic_now=None):
    rule = connector.trading_rules.get(pair)
    if (connector.name != 'okx_perpetual' or rule is None
            or rule.buy_order_collateral_token != 'USDT' or rule.sell_order_collateral_token != 'USDT'):
        raise ValueError('reduction_fee_unavailable')
    symbol = await connector.exchange_symbol_associated_to_pair(pair)
    if symbol != pair + '-SWAP' or not pair.endswith('-USDT'):
        raise ValueError('reduction_fee_unavailable')
    result = await connector._api_get(path_url='/api/v5/account/trade-fee',
                                      params={'instType': 'SWAP', 'instFamily': symbol[:-5]},
                                      is_auth_required=True, trading_pair=pair)
    return FeeObservation.decode(result, wall_now=time.time() if wall_now is None else wall_now,
                                 monotonic_now=time.monotonic() if monotonic_now is None else monotonic_now)

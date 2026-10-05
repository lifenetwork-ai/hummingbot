"""P4 rolling fills limit replenishment without resetting on cooldown."""

from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.risk import RollingFillLimiter


def test_repeated_buy_fills_block_risk_increasing_buy_replenishment():
    limiter = RollingFillLimiter(window_ms=60000, max_filled_base=Decimal("10"))
    assert limiter.record_fill("trade-a", "BUY", Decimal("6"), observed_monotonic_ms=1000)
    assert limiter.record_fill("trade-b", "BUY", Decimal("4"), observed_monotonic_ms=2000)
    assert not limiter.record_fill("trade-b", "BUY", Decimal("4"), observed_monotonic_ms=2000)
    assert limiter.can_replenish("BUY", Decimal("1"), current_inventory_base=Decimal("100"),
                                 target_inventory_base=Decimal("100"), now_monotonic_ms=3000) is False
    assert limiter.can_replenish("SELL", Decimal("1"), current_inventory_base=Decimal("100"),
                                 target_inventory_base=Decimal("100"), now_monotonic_ms=3000)
    # A cooldown or data recovery does not erase the rolling fills.
    assert not limiter.can_replenish("BUY", Decimal("1"), current_inventory_base=Decimal("100"),
                                     target_inventory_base=Decimal("100"), now_monotonic_ms=30000)
    assert limiter.can_replenish("BUY", Decimal("1"), current_inventory_base=Decimal("100"),
                                 target_inventory_base=Decimal("100"), now_monotonic_ms=63000)


def test_inventory_reduction_is_not_blocked_by_opposite_side_fill_limit():
    limiter = RollingFillLimiter(window_ms=60000, max_filled_base=Decimal("10"))
    limiter.record_fill("trade-a", "BUY", Decimal("10"), observed_monotonic_ms=1000)
    assert limiter.can_replenish("SELL", Decimal("2"), current_inventory_base=Decimal("120"),
                                 target_inventory_base=Decimal("100"), now_monotonic_ms=2000)

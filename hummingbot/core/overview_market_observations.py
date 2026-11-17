"""Bounded, read-only market observations for the admin Overview response."""
import math
import time
from datetime import datetime, timezone
from decimal import Decimal

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.mm_liquidity import own_quotes
from hummingbot.strategy_v2.utils.quote_guard import _orders, observe_market

_DEPTH_OFFSETS_BPS = (25, 50, 100)
_SLIPPAGE_NOTIONALS_USDT = (Decimal("100"), Decimal("1000"), Decimal("10000"))


def _stamp(value):
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return None


def _decimal_string(value):
    return format(value, "f")


def _observation(value, observed_at, reason=None):
    return dict(value=value, observed_at=observed_at, stale=value is None, unavailable_reason=reason)


def _unavailable(observed_at, reason):
    return _observation(None, observed_at, reason)


def _cached_connector(provider, name):
    """Read an existing trading or cached public connector without LazyDict creation."""
    connector = provider.connectors.get(name)
    if connector is not None:
        return connector
    cached = getattr(provider, "_non_trading_connectors", None)
    if isinstance(cached, dict):
        return dict.get(cached, name)
    return None


def _market_observation(controller, now):
    config = controller.config
    provider = controller.market_data_provider
    connector_name = config.connector_name
    pair = config.trading_pair
    connector = _cached_connector(provider, connector_name)
    if connector is None:
        raise ValueError("connector_unavailable")
    books = getattr(connector, "order_books", None)
    book = books.get(pair) if isinstance(books, dict) else None
    if book is None:
        raise ValueError("order_book_unavailable")
    max_age = float(config.max_data_age_seconds)
    max_levels = config.quote_guards.max_book_levels
    max_orders = config.quote_guards.max_own_orders
    qualified = observe_market(connector, book, pair, now=now, max_age_seconds=max_age,
                               max_levels=max_levels, max_orders=max_orders)
    if qualified.market_mid is None:
        raise ValueError(qualified.detail or "market_price_unavailable")
    # Reuse the established conservative own-order filter for bot depth. This
    # reads acknowledged in-memory orders only and never changes their state.
    own = own_quotes(connector, pair, max_orders=max_orders)
    midpoint = qualified.market_mid
    levels = []
    for offset in _DEPTH_OFFSETS_BPS:
        distance = midpoint * Decimal(offset) / Decimal(10000)
        bid_floor, ask_ceiling = midpoint - distance, midpoint + distance
        bid_ceiling = midpoint + distance
        ask_floor = midpoint - distance
        bid = sum((Decimal(row["remaining_notional"]) for row in own["working_orders"]
                   if row["side"] == "BUY" and bid_floor <= Decimal(row["price"]) <= bid_ceiling), Decimal("0"))
        ask = sum((Decimal(row["remaining_notional"]) for row in own["working_orders"]
                   if row["side"] == "SELL" and ask_floor <= Decimal(row["price"]) <= ask_ceiling), Decimal("0"))
        levels.append(dict(offset_bps=offset, bid_usdt=_decimal_string(bid), ask_usdt=_decimal_string(ask)))
    raw_bids, raw_asks = qualified.token[1]
    known_own = {TradeType.BUY: {}, TradeType.SELL: {}}
    for _, _, side, _, price, amount, _, _ in _orders(connector, pair, max_orders):
        projected = Decimal(str(float(price)))
        known_own[side][projected] = known_own[side].get(projected, Decimal('0')) + amount
    estimates = []
    for side, rows in (("BUY", raw_asks), ("SELL", raw_bids)):
        for notional in _SLIPPAGE_NOTIONALS_USDT:
            target_base = notional / midpoint
            remaining = target_base
            executed = Decimal("0")
            quote = Decimal("0")
            for price, quantity in rows:
                order_side = TradeType.SELL if side == 'BUY' else TradeType.BUY
                # Full known own size is a conservative bound for pending,
                # partially filled and cached terminal orders as in observe_market.
                available = max(Decimal('0'), quantity - known_own[order_side].get(price, Decimal('0')))
                if available <= Decimal(str(math.ulp(float(quantity)))) * 2:
                    continue
                take = min(available, remaining)
                if take > 0:
                    executed += take
                    quote += take * price
                    remaining -= take
                if remaining <= 0:
                    break
            enough = remaining <= 0
            slippage = None
            if enough and executed > 0:
                reference_quote = executed * midpoint
                adverse = quote - reference_quote if side == "BUY" else reference_quote - quote
                slippage = adverse / reference_quote * Decimal(10000)
            estimates.append(dict(side=side, notional_usdt=_decimal_string(notional),
                                  expected_slippage_bps=_decimal_string(slippage) if slippage is not None else None,
                                  sufficient_depth=enough))
    return dict(
        market="spot" if connector_name == "okx" else "perpetual",
        connector=connector_name,
        pair=pair,
        depth=_observation(dict(basis="own_working_orders", reference_mid=_decimal_string(midpoint), levels=levels), _stamp(qualified.book_at)),
        coverage=_unavailable(_stamp(now), "rolling_coverage_history_unavailable"),
        slippage_estimate=_observation(dict(basis="exchange_order_book", reference_mid=_decimal_string(midpoint),
                                            estimates=estimates), _stamp(qualified.book_at)),
        post_fill_recovery=_unavailable(_stamp(now), "post_fill_recovery_history_unavailable"),
    ), qualified


def _valuation_price(controller, now):
    """Value LIFE from its configured existing spot reference book without own-depth."""
    config = controller.config
    provider = controller.market_data_provider
    connector_name = config.reference_connector
    connector = _cached_connector(provider, connector_name)
    if connector is None:
        raise ValueError("valuation_reference_connector_unavailable")
    books = getattr(connector, "order_books", None)
    pair = config.trading_pair
    book = books.get(pair) if isinstance(books, dict) else None
    if book is None:
        raise ValueError("valuation_reference_book_unavailable")
    qualified = observe_market(connector, book, pair, now=now,
                               max_age_seconds=float(config.max_data_age_seconds),
                               max_levels=config.quote_guards.max_book_levels,
                               max_orders=config.quote_guards.max_own_orders)
    if qualified.market_mid is None:
        raise ValueError(qualified.detail or "valuation_reference_price_unavailable")
    return dict(asset=pair.split("-")[0], pair=pair, connector=connector_name,
                price_usdt=_decimal_string(qualified.market_mid), observed_at=_stamp(qualified.book_at))


def _references(controllers, session, now):
    requested = []
    anchors = session.anchors if isinstance(getattr(session, "anchors", None), dict) else {}
    for controller in controllers:
        config = controller.config
        references = getattr(config, "references", ())
        if not references:
            continue
        saved = anchors.get(config.id)
        # Session start is the only persisted anchor timestamp. A controller
        # must prove that its active config belongs to this exact session step.
        request = getattr(session, "request", None)
        if (getattr(config, "mm_session_id", None) != session.id
                or getattr(config, "mm_generation", None) != session.generation
                or getattr(request, "selection", None) == "random" and session.step_index > 0
                or not isinstance(saved, dict) or not session.started_at
                or not math.isfinite(float(session.started_at))):
            raise ValueError("reference_anchor_unavailable")
        source_anchors = saved.get("sources")
        if not isinstance(source_anchors, dict):
            raise ValueError("reference_anchor_unavailable")
        provider = controller.market_data_provider
        reference_connector = config.reference_connector
        connector = _cached_connector(provider, reference_connector)
        if connector is None:
            raise ValueError("reference_connector_unavailable")
        books = getattr(connector, "order_books", None)
        if not isinstance(books, dict):
            raise ValueError("reference_order_book_unavailable")
        for reference in references:
            pair = reference.pair
            if pair not in source_anchors:
                raise ValueError("reference_anchor_unavailable")
            book = books.get(pair)
            if book is None:
                raise ValueError("reference_order_book_unavailable")
            observed = observe_market(connector, book, pair, now=now,
                                      max_age_seconds=float(config.max_data_age_seconds),
                                      max_levels=config.quote_guards.max_book_levels,
                                      max_orders=config.quote_guards.max_own_orders)
            anchor = Decimal(str(source_anchors[pair]))
            if observed.market_mid is None or not anchor.is_finite() or anchor <= 0:
                raise ValueError(observed.detail or "reference_price_unavailable")
            requested.append(dict(controller_id=config.id, connector=reference_connector, pair=pair,
                                  anchor_price=_decimal_string(anchor), last_price=_decimal_string(observed.market_mid),
                                  anchor_at=_stamp(float(session.started_at)), price_observed_at=_stamp(observed.book_at)))
    return requested


def _inventory_limits(controllers):
    limits = []
    for controller in controllers:
        config = controller.config
        market = "spot" if config.connector_name == "okx" else "perpetual"

        def encoded_limit(name):
            raw = getattr(config, name, None)
            if raw is None:
                return None
            value = Decimal(str(raw))
            if not value.is_finite() or value <= 0:
                raise ValueError("inventory_limit_unavailable")
            return _decimal_string(value)

        spot_limit = encoded_limit("max_base_inventory") if market == "spot" else None
        limits.append(dict(controller_id=config.id, market=market, pair=config.trading_pair,
                           asset=config.trading_pair.split("-")[0], inventory_limit=spot_limit,
                           long_limit=encoded_limit("max_long_base") if market == "perpetual" else None,
                           short_limit=encoded_limit("max_short_base") if market == "perpetual" else None,
                           gross_limit=encoded_limit("max_gross_base") if market == "perpetual" else None))
    return limits


def capture_overview_markets(host, *, now=None):
    """Read cached books and own orders; collector failures never reach trading paths."""
    now = time.time() if now is None else float(now)
    observed_at = _stamp(now) if math.isfinite(now) else None
    session = getattr(host, "mm_session", None)
    if session is None:
        unknown = _unavailable(observed_at, "session_unavailable")
        return dict(session_id=None, generation=None, observed_at=observed_at, markets=[],
                    references=unknown, inventory_limits=unknown, valuation_prices=unknown)
    controllers = list(host._mm_controllers())
    markets = []
    for controller in controllers:
        try:
            market, _ = _market_observation(controller, now)
        except Exception as error:
            reason = str(error) if isinstance(error, ValueError) and str(error) else "market_observation_unavailable"
            stamp = observed_at
            market_name = "spot" if controller.config.connector_name == "okx" else "perpetual"
            market = dict(market=market_name, connector=controller.config.connector_name, pair=controller.config.trading_pair,
                          depth=_unavailable(stamp, reason), coverage=_unavailable(stamp, "rolling_coverage_history_unavailable"),
                          slippage_estimate=_unavailable(stamp, reason),
                          post_fill_recovery=_unavailable(stamp, "post_fill_recovery_history_unavailable"))
        markets.append(market)
    try:
        valuation_value = []
        for controller in controllers:
            if controller.config.reference_connector == "okx":
                price = _valuation_price(controller, now)
                prior = next((item for item in valuation_value if (item['asset'], item['pair'], item['connector']) ==
                              (price['asset'], price['pair'], price['connector'])), None)
                if prior is not None and prior != price:
                    raise ValueError('valuation_source_conflict')
                if prior is None:
                    valuation_value.append(price)
        valuation_prices = _observation(valuation_value, observed_at) if valuation_value else _unavailable(
            observed_at, "spot_valuation_book_unavailable")
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) else "valuation_price_unavailable"
        valuation_prices = _unavailable(observed_at, reason)
    try:
        reference_value = _references(controllers, session, now)
        references = _observation(reference_value, min((item['price_observed_at'] for item in reference_value), default=observed_at))
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) else "reference_observation_unavailable"
        references = _unavailable(observed_at, reason)
    try:
        inventory_value = _inventory_limits(controllers)
        inventory_limits = _observation(inventory_value, observed_at)
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) else "inventory_limit_unavailable"
        inventory_limits = _unavailable(observed_at, reason)
    return dict(session_id=session.id, generation=session.generation, observed_at=observed_at,
                markets=markets, references=references, inventory_limits=inventory_limits,
                valuation_prices=valuation_prices)

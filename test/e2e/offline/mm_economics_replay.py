"""Controller-level replay adapter; transport/executor/DB E2E stays separate."""
import asyncio
import hashlib
import json
from collections import Counter, deque
from decimal import ROUND_CEILING, Decimal
from functools import lru_cache
from pathlib import Path
from test.e2e.offline.exchange import Exchange
from test.e2e.offline.mm_economics import PassiveOrder, Wallet, markouts
from test.e2e.offline.mm_economics_scenarios import scenario_tape, tape_hash
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from controllers.market_making.life_mm import LifeMMConfig, LifeMMController
from hummingbot.client.config.mm_profiles import MMProfilesConfig
from hummingbot.core.data_type.common import PositionMode, PositionSide, PriceType, TradeType
from hummingbot.core.data_type.order_book import OrderBook
from hummingbot.core.data_type.order_book_row import OrderBookRow
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.spot_outflow import SpotOutflowConfig, SpotOutflowProjection
from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
from hummingbot.strategy_v2.models.base import RunnableStatus
from scripts.mm_load_ledger import funding_ledger_violations, ledger_violations

D, ZERO, EPOCH = Decimal, Decimal('0'), 1800000000
QUANTUM, CONTRACT = D('.000001'), D('.01')


class LocalConnector:
    def __init__(self, name, provider):
        self.name, self.provider = name, provider
        self.is_trading_required = True
        self.in_flight_orders = {}
        self._set_position_mode_lock = asyncio.Lock()
        self._perpetual_trading = SimpleNamespace(set_position_mode=lambda *_: None, set_leverage=lambda *_: None)
        self.trading_rules = {pair: SimpleNamespace(min_order_size=QUANTUM, min_base_amount_increment=QUANTUM,
                                                    min_notional_size=D('.001'))
                              for pair in ('LIFE-USDT', 'BTC-USDT', 'ETH-USDT', 'AVAX-USDT')}

    async def _fetch_account_position_mode(self):
        return PositionMode.HEDGE

    async def _set_trading_pair_leverage(self, *_):
        return True, ''

    @property
    def account_positions(self):
        values = {}
        for side, hold in self.provider.wallet.holds.items():
            if hold.net_amount_base:
                values[side] = SimpleNamespace(trading_pair='LIFE-USDT', amount=hold.net_amount_base,
                                               position_side=PositionSide.LONG if side == 'BUY' else PositionSide.SHORT)
        return values

    @property
    def account_balance_observations(self):
        return {asset: dict(cash_balance=value, observed_at=self.provider.time()) for asset, value in (
            ('LIFE', self.provider.wallet.base), ('USDT', self.provider.wallet.cash))}

    def get_balance(self, asset):
        return self.provider.wallet.base if asset == 'LIFE' else self.provider.wallet.cash

    def get_available_balance(self, asset):
        return self.provider.available(asset)

    def quantize_order_price(self, pair, price):
        return price // QUANTUM * QUANTUM

    def quantize_order_amount(self, pair, amount):
        return amount // QUANTUM * QUANTUM

    def get_order_price_quantum(self, *_):
        return QUANTUM

    def get_fee(self, **_):
        return AddedToCostTradeFee(percent=self.provider.assumptions.maker_fee_rate)


class LocalProvider:
    ready = True

    def __init__(self, wallet, assumptions, cfg):
        self.wallet, self.assumptions = wallet, assumptions
        self.cfg = cfg
        self.now, self.mark, self.orders = EPOCH, wallet.initial_mark, []
        self.candles_feeds, self.books = {}, {}
        self.connectors = {name: LocalConnector(name, self) for name in ('okx', 'okx_perpetual')}
        self.feed = SimpleNamespace(_candles=deque(maxlen=120), interval_in_seconds=1)
        self.leverage = 2

    def time(self):
        return self.now

    def initialize_rate_sources(self, *_):
        pass  # All conversion prices are supplied by the tape; no network.

    def get_connector_with_fallback(self, name):
        return self.connectors[name]

    async def acquire_order_books(self, connector, pairs, _):
        return dict.fromkeys(pairs, True)

    async def release_order_books(self, *_):
        pass

    def _generate_candle_feed_key(self, config):
        return f'{config.connector}_{config.trading_pair}_{config.interval}'

    def acquire_candle_feed(self, config, _):
        self.candles_feeds[self._generate_candle_feed_key(config)] = self.feed

    def release_candle_feed(self, *_):
        pass

    def get_order_book(self, connector, pair):
        return self.books[(connector, pair)]

    def get_price_by_type(self, connector, pair, price_type):
        book = self.get_order_book(connector, pair)
        if price_type == PriceType.MidPrice:
            return (D(str(book.get_price(False))) + D(str(book.get_price(True)))) / 2
        return D(str(book.get_price(price_type == PriceType.BestAsk)))

    def observe(self, frame):
        previous = self.mark
        self.now, self.mark = EPOCH + frame['at'], D(frame['mark'])
        # Candle ending at now contains only observations already received.
        if frame['at']:
            self.feed._candles.append([self.now - 1, float(previous), float(max(previous, self.mark)),
                                       float(min(previous, self.mark)), float(self.mark)])
        for connector in self.connectors:
            for pair, value in {'LIFE-USDT': frame['mark'], **frame['references']}.items():
                stamp = self.now * (1000 if connector == 'okx_perpetual' else 1)
                book = self.books.setdefault((connector, pair), OrderBook())
                rows = pair != 'LIFE-USDT' or frame['external_book']
                mid = D(value)
                bid, ask = self.external_prices(mid)
                size = float(self.cfg.external_depth_base)
                book.apply_snapshot([OrderBookRow(float(bid), size, stamp)] if rows else [],
                                    [OrderBookRow(float(ask), size, stamp)] if rows else [], stamp)

    def external_prices(self, mark):
        bid = mark * (1 - self.cfg.external_half_spread) // QUANTUM * QUANTUM
        ask = (mark * (1 + self.cfg.external_half_spread) / QUANTUM).to_integral_value(rounding=ROUND_CEILING) * QUANTUM
        return bid, ask

    def available(self, asset):
        pending = [order for order in self.orders if order.live(self.now - EPOCH)]
        if asset == 'LIFE':
            return max(ZERO, self.wallet.base - sum((o.remaining for o in pending if o.market == 'spot' and o.side == 'SELL'), ZERO))
        held_margin = sum((abs(h.net_amount_base) * h.avg_entry_price / self.leverage for h in self.wallet.holds.values()), ZERO)
        pending_margin = sum((o.remaining * o.price * (D('1') / (self.leverage if o.market == 'perpetual' else 1)
                              + max(ZERO, self.assumptions.maker_fee_rate))
                              for o in pending if o.market == 'perpetual' or o.side == 'BUY'), ZERO)
        return max(ZERO, self.wallet.cash + self.wallet.unrealized(self.mark) - held_margin - pending_margin)


def controller_config(cfg, profile, market, variant):
    references = ([] if profile == 'sideways' else [cfg.trend_reference] if profile == 'follow_token' else cfg.market_references)
    base = deployed_catalogue().controller_configs(profile, market, None, references=references)[0]
    changes = dict(
        id='economic_' + market, connector_name='okx' if market == 'spot' else 'okx_perpetual',
        trading_pair='LIFE-USDT', scenario=profile, references=references,
        total_amount_quote=cfg.quote_budget_per_market, buy_spreads=cfg.spreads, sell_spreads=cfg.spreads,
        executor_refresh_time=cfg.refresh_seconds, cooldown_time=0, leverage=1 if market == 'spot' else 2,
        min_price=cfg.initial_mark / 10, max_price=cfg.initial_mark * 10, initial_anchor=cfg.initial_mark,
        max_base_inventory=cfg.max_spot_base, max_long_base=cfg.max_perpetual_side_base,
        max_short_base=cfg.max_perpetual_side_base, max_gross_base=cfg.max_perpetual_gross_base,
        runtime_config_managed=True, mm_session_id='economic', mm_generation=1,
        quote_guards=dict(enabled=True, mode='bootstrap_then_market', max_deviation_bps='2500',
                          resume_deviation_bps='2000', recovery_delay_seconds='1',
                          bootstrap=dict(duration_seconds=cfg.bootstrap_seconds, max_anchor_move_bps='2000',
                                         max_quote=cfg.quote_budget_per_market / 2, min_external_depth_quote='1',
                                         max_market_spread_bps='500', qualification_seconds='2')),
        spread_policy=dict(fee_floor=variant.fee_floor,
                           volatility=dict(enabled=variant.volatility, interval='1s', length=14, target_natr='.002',
                                           min_multiplier='1', max_multiplier='3', max_age_seconds=5,
                                           on_unavailable='static', fallback_buy_spreads=cfg.spreads, fallback_sell_spreads=cfg.spreads)),
        inventory_skew=dict(enabled=market == 'spot' and variant.inventory_skew, target_base_pct='.5', inventory_range_multiplier='1'))
    return LifeMMConfig.model_validate({**base.model_dump(), **changes})


@lru_cache(maxsize=1)
def deployed_catalogue():
    root = Path(__file__).resolve().parents[3]
    source = root / 'deploy/cloudrun/conf/life_mm_profiles.yaml'
    if not source.exists():
        source = root / 'conf/life_mm_profiles.yaml'
    return MMProfilesConfig.model_validate(yaml.safe_load(source.read_text()))


def liquidity_sample(orders, now, mark, cfg):
    sides = {side: [o for o in orders if o.side == side and o.live(now) and o.active_at <= now] for side in ('BUY', 'SELL')}
    if not all(sides.values()):
        return False
    bid, ask = max(o.price for o in sides['BUY']), min(o.price for o in sides['SELL'])
    # The independent mark must be inside the quoted market, not just a narrow
    # spread around a stale model reference far away from the scenario price.
    band = cfg.max_quoted_spread_bps / 20000
    return (bid < mark < ask and (ask - bid) / mark * 10000 <= cfg.max_quoted_spread_bps
            and all(sum((o.remaining * o.price for o in values if abs(o.price / mark - 1) <= band), ZERO)
                    >= cfg.minimum_side_quote for values in sides.values()))


async def run_case(cfg, *, scenario, seed, profile, market_mode, variant, sensitivity):
    from test.e2e.offline.mm_economics import match_flow, post_only_crosses
    assumptions, policy = cfg.sensitivities[sensitivity], cfg.variants[variant]
    drain = max(max(value.cancel_latency_seconds * (3 if scenario == 'gap_slow_cancel' else 1),
                    value.placement_latency_seconds) for value in cfg.sensitivities.values())
    tape = scenario_tape(scenario, seed, cfg.duration_seconds, cfg.initial_mark)
    # Hold final exogenous observation during a common, finite cancellation tail.
    tape += [{**tape[-1], 'at': cfg.duration_seconds + extra} for extra in range(1, drain + 1)]
    wallet = Wallet(cash=cfg.initial_cash, base=cfg.initial_base, initial_mark=cfg.initial_mark)
    provider = LocalProvider(wallet, assumptions, cfg)
    details = [dict(ccy=asset, eq=str(amount), cashBal=str(amount), availEq=str(amount), availBal=str(amount))
               for asset, amount in (('LIFE', cfg.initial_base), ('USDT', cfg.initial_cash))]
    exchange = Exchange(fixtures={'balances': {'data': [{'details': details}]}, 'order_event': {'arg': {}, 'data': [{}]}})
    markets = ('spot', 'perpetual') if market_mode == 'both' else (market_mode,)
    controllers = {market: LifeMMController(controller_config(cfg, profile, market, policy), provider, asyncio.Queue()) for market in markets}
    outflow = SpotOutflowProjection(SpotOutflowConfig(window_seconds=cfg.outflow_window_seconds,
                                                      max_net_outflow_quote=cfg.outflow_cap_quote, fee_allowance_rate=cfg.fee_allowance_rate), 'LIFE-USDT',
                                    coverage_start_ms=(EPOCH - cfg.outflow_window_seconds) * 1000)
    orders, rows, fills, funding_rows, equity, buy_admissions = [], [], [], [], [], []
    reasons = Counter()
    stats = {market: dict(elapsed_seconds=cfg.duration_seconds, eligible_seconds=0, qualified_seconds=0,
                          eligible_qualified_seconds=0) for market in markets}
    peak_outflow, volume, equity_peak, drawdown, cancellations, rejections = ZERO, ZERO, wallet.equity(cfg.initial_mark), ZERO, 0, 0
    min_cash, min_base, max_base, peak_gross, min_margin = wallet.cash, wallet.base, wallet.base, ZERO, wallet.cash
    joint_quality, expired_placements, outflow_above_cap = 0, 0, 0
    with patch('controllers.market_making.life_mm.time', SimpleNamespace(monotonic=provider.time, time=provider.time)), \
            patch('test.e2e.offline.exchange.millis', lambda: str(int(provider.time() * 1000))):
        provider.observe(tape[0])
        for controller in controllers.values():
            await controller.on_start()
            controller.initialize_candles()
            controller.restore_bootstrap_episode(dict(started_at=EPOCH, expires_at=EPOCH + cfg.bootstrap_seconds, market_qualified=False))
            controller.admit_session('economic', 1)
        try:
            for frame in tape:
                tick, mark = frame['at'], D(frame['mark'])
                provider.observe(frame)
                exchange.prices.update({'LIFE-USDT': frame['mark'], **frame['references']})
                exchange.mark_positions()
                outflow.advance((EPOCH + tick) * 1000)
                for order_index, order in enumerate(orders):
                    remote = exchange.orders[order.id]
                    if remote['state'] in ('live', 'partially_filled') and not order.live(tick):
                        remote['state'] = 'filled' if order.remaining == 0 else 'canceled'
                        if order.market == 'spot' and order.side == 'BUY':
                            outflow.confirm_terminal(order.id, order.filled)
                    if order.live(tick) and order.active_at == tick:
                        bid, ask = provider.external_prices(mark) if frame['external_book'] else (None, None)
                        crossing = post_only_crosses(order, orders[:order_index], tick, bid, ask)
                        expired = order.create_deadline is not None and tick >= order.create_deadline
                        if crossing or expired:
                            order.cancel_at, remote['state'] = tick, 'canceled'
                            if order.market == 'spot' and order.side == 'BUY':
                                outflow.confirm_terminal(order.id, order.filled)
                            rejections += int(bool(crossing))
                            expired_placements += int(expired)
                # Existing orders may fill before a newly observed guard breach
                # reaches cancellation acknowledgement. No hindsight cancellation.
                for market in markets:
                    flow = frame['flows'].get(market)
                    if flow is None or tick == 0:
                        continue
                    matches = match_flow([o for o in orders if o.market == market], now=tick, aggressor=flow['side'],
                                         limit=D(flow['limit']), quantity=D(flow['amount']), fraction=assumptions.max_fill_fraction_per_event,
                                         quantum=QUANTUM, external_levels=[(provider.external_prices(mark)[0 if flow['side'] == 'SELL' else 1], cfg.external_depth_base)])
                    for order, amount in matches:
                        fee = amount * order.price * assumptions.maker_fee_rate
                        fill_id = str(len(fills) + 1)
                        wallet.fill(fill_id, market, order.side, amount, order.price, fee)
                        await exchange.control({'fill': {'clOrdId': order.id, 'amount': str(amount / (CONTRACT if market == 'perpetual' else 1)), 'fee': str(fee)}})
                        row = dict(market='okx' if market == 'spot' else 'okx_perpetual', order_id=order.id,
                                   symbol='LIFE-USDT', exchange_trade_id=fill_id, timestamp=(EPOCH + tick) * 1000,
                                   trade_type=order.side, amount=str(amount), price=str(order.price),
                                   trade_fee_in_quote=str(fee), trade_fee=dict(percent='0', flat_fees=[dict(token='USDT', amount=str(fee))]))
                        rows.append(row)
                        fills.append(dict(at=tick, market=market, side=order.side, amount=str(amount), price=str(order.price), fee=str(fee)))
                        volume += amount * order.price
                        if market == 'spot':
                            outflow.add_fill(row)
                            if order.remaining == 0 and order.side == 'BUY':
                                outflow.confirm_terminal(order.id, order.filled)
                if tick > 0 and tick % cfg.funding_interval_seconds == 0:
                    net = sum((D(p['pos']) * CONTRACT * (-1 if p['posSide'] == 'short' else 1) for p in exchange.positions.values()), ZERO)
                    payment = -net * mark * assumptions.funding_rate_per_event
                    assert abs(payment - wallet.fund(mark, assumptions.funding_rate_per_event)) < D('1e-18')
                    if net:
                        bill = dict(billId=str(len(funding_rows) + 1), type='8', subType='174' if payment >= 0 else '173',
                                    instId='LIFE-USDT-SWAP', ts=str((EPOCH + tick) * 1000), pnl=str(payment), ccy='USDT')
                        exchange.funding_bills.append(bill)
                        funding_rows.append(dict(account_id='synthetic', environment='demo', bill_id=bill['billId'],
                                                 symbol='LIFE-USDT', timestamp=int(bill['ts']), amount=str(payment), currency='USDT'))
                provider.orders = orders
                for market, controller in controllers.items():
                    controller.executors_info = [SimpleNamespace(id=o.id, timestamp=EPOCH + o.created_at,
                                                                 config=o.config, is_active=True, is_trading=o.filled > 0, status=RunnableStatus.RUNNING,
                                                                 custom_info={'level_id': o.level}, close_type=None) for o in orders if o.market == market and o.live(tick)]
                    controller.update_reference(sample_guard=True)
                    if controller.processed_data.get('internal_error'):
                        raise RuntimeError('Controller internal error: ' + controller.processed_data['reason'])
                    guard = controller.processed_data.get('quote_guard') or {}
                    bootstrap = guard.get('bootstrap') or {}
                    if guard.get('qualification_pending'):
                        controller.restore_bootstrap_episode(dict(started_at=bootstrap['started_at'], expires_at=bootstrap['expires_at'], market_qualified=True))
                        controller.update_reference()
                    if not controller.processed_data.get('ready'):
                        reasons[controller.processed_data.get('reason', 'not_ready')] += 1
                ready = all(c.processed_data.get('ready') and c.exposure_within_caps() for c in controllers.values()) and tick < cfg.duration_seconds
                for market, controller in controllers.items():
                    actions = controller.determine_executor_actions() if ready else controller.stop_quote_actions()
                    for action in actions:
                        if hasattr(action, 'executor_id'):
                            order = next(o for o in orders if o.id == action.executor_id)
                            if order.cancel_at is None:
                                order.cancel_at = tick + (assumptions.cancel_latency_seconds * (3 if scenario == 'gap_slow_cancel' else 1))
                                # Tail must cover the declared session-expiry cancellation.
                                if tick >= cfg.duration_seconds:
                                    order.cancel_at = tick + assumptions.cancel_latency_seconds
                                cancellations += 1
                        elif ready:
                            config = action.executor_config
                            # Reuse the executor's existing maker-entry convention.
                            shim = SimpleNamespace(config=config, _open_order=None,
                                                   get_price=lambda cn, pair, pt: provider.get_price_by_type(cn, pair, pt))
                            price = PositionExecutor.entry_price.fget(shim)
                            amount = provider.connectors[config.connector_name].quantize_order_amount('LIFE-USDT', config.amount)
                            price = provider.connectors[config.connector_name].quantize_order_price('LIFE-USDT', price)
                            if amount <= 0 or amount * price < D('.001'):
                                continue
                            if market == 'perpetual' or config.side == TradeType.BUY:
                                cost = amount * price * (D('1') / config.leverage + max(ZERO, assumptions.maker_fee_rate))
                                if cost > provider.available('USDT'):
                                    reasons['collateral_with_fees'] += 1
                                    continue
                            identifier = str(len(orders) + 1)
                            if market == 'spot' and config.side == TradeType.BUY:
                                if not outflow.reserve(SimpleNamespace(client_order_id=identifier, amount=amount, price=price)):
                                    reasons['spot_outflow_cap'] += 1
                                    continue
                                assert max(ZERO, outflow.net) + outflow.reserved() <= cfg.outflow_cap_quote
                                buy_admissions.append(dict(order_id=identifier, at=tick, committed_positive=str(max(ZERO, outflow.net)),
                                                           reserved_after=str(outflow.reserved()), cap=str(cfg.outflow_cap_quote)))
                            config = config.model_copy(update={'id': identifier, 'entry_price': price, 'amount': amount})
                            order = PassiveOrder(identifier, market, config.side.name, price, amount, tick,
                                                 tick + assumptions.placement_latency_seconds, assumptions.queue_ahead_base,
                                                 level=config.level_id, config=config)
                            captured = (controller.processed_data.get('quote_guard') or {}).get('bootstrap') or {}
                            order.create_deadline = min(cfg.duration_seconds,
                                                        int(captured['expires_at'] - EPOCH) if not captured.get('market_qualified', True) else cfg.duration_seconds)
                            orders.append(order)
                            exchange.orders[identifier] = dict(clOrdId=identifier, ordId=identifier,
                                                               instId='LIFE-USDT' + ('-SWAP' if market == 'perpetual' else ''), side=config.side.name.lower(),
                                                               posSide='long' if config.side == TradeType.BUY else 'short', state='live', accFillSz='0',
                                                               px=str(price), sz=str(amount / (CONTRACT if market == 'perpetual' else 1)))
                peak_outflow = max(peak_outflow, max(ZERO, outflow.net) + outflow.reserved())
                # Expiring earlier SELL credits can raise the rolling net after
                # valid earlier buys. Only new BUY admission is constrained.
                outflow_above_cap += int(max(ZERO, outflow.net) + outflow.reserved() > cfg.outflow_cap_quote)
                min_cash, min_base, max_base = min(min_cash, wallet.cash), min(min_base, wallet.base), max(max_base, wallet.base)
                peak_gross = max(peak_gross, sum((abs(h.net_amount_base) for h in wallet.holds.values()), ZERO))
                margin = wallet.cash + wallet.unrealized(mark) - sum((abs(h.net_amount_base) * h.avg_entry_price / 2 for h in wallet.holds.values()), ZERO)
                min_margin = min(min_margin, margin)
                operating = cfg.operating_cost_per_hour_quote * D(tick) / 3600
                value = wallet.equity(mark) - operating
                equity_peak = max(equity_peak, value)
                drawdown = max(drawdown, equity_peak - value)
                equity.append(dict(at=tick, mark=str(mark), cash=str(wallet.cash), spot_base=str(wallet.base), equity_after_cost=str(value)))
                if tick < cfg.duration_seconds:
                    qualified_markets = []
                    for market in markets:
                        qualified = liquidity_sample([o for o in orders if o.market == market], tick, mark, cfg)
                        qualified_markets.append(qualified)
                        stats[market]['eligible_seconds'] += int(frame['external_book'])
                        stats[market]['qualified_seconds'] += int(qualified)
                        stats[market]['eligible_qualified_seconds'] += int(qualified and frame['external_book'])
                    joint_quality += int(all(qualified_markets))
            # The finite replay never assumes remaining orders/positions vanished.
            assert not any(order.live(tape[-1]['at']) for order in orders)
        finally:
            for controller in controllers.values():
                await controller.on_stop()
    assert not ledger_violations({'fills': exchange.fills}, rows)
    assert not funding_ledger_violations(exchange.funding_bills, funding_rows, account_id='synthetic', environment='demo')
    cash = next(row for row in exchange.fixtures['balances']['data'][0]['details'] if row['ccy'] == 'USDT')
    assert abs(D(cash['cashBal']) + sum((D(bill['pnl']) for bill in exchange.funding_bills), ZERO) - wallet.cash) < D('1e-18')
    assert abs(sum((D(p['upl']) for p in exchange.positions.values()), ZERO) - wallet.unrealized(provider.mark)) < D('1e-18')
    for market, values in stats.items():
        values['wall_time_coverage'] = str(D(values['qualified_seconds']) / cfg.duration_seconds)
        # Zero eligible market time is unknown, never 100% successful coverage.
        values['eligible_coverage'] = (str(D(values['eligible_qualified_seconds']) / values['eligible_seconds']) if values['eligible_seconds'] else None)
    final, hold = wallet.equity(provider.mark), wallet.hold_equity(provider.mark)
    operating = cfg.operating_cost_per_hour_quote * D(tape[-1]['at']) / 3600
    net_cost = wallet.fees - wallet.funding + operating
    market_results = {}
    for market in markets:
        selected = [row for row in fills if row['market'] == market]
        fees = sum((D(row['fee']) for row in selected), ZERO)
        if market == 'spot':
            cash_flow = sum((D(row['amount']) * D(row['price']) * (-1 if row['side'] == 'BUY' else 1) - D(row['fee'])
                             for row in selected), ZERO)
            contribution = cash_flow + (wallet.base - wallet.initial_base) * provider.mark
        else:
            contribution = wallet.unrealized(provider.mark) - fees + wallet.funding
        market_results[market] = dict(fills=len(selected), fees_quote=str(fees),
                                      funding_quote=str(wallet.funding) if market == 'perpetual' else None,
                                      incremental_pnl_vs_hold_before_operating_cost_quote=str(contribution),
                                      markouts=markouts(selected, {row['at']: D(row['mark']) for row in tape}))
    assert abs(sum((D(row['incremental_pnl_vs_hold_before_operating_cost_quote']) for row in market_results.values()), ZERO)
               - (final - hold)) < D('1e-18')
    effective = {market: dict(config=controller.config.model_dump(mode='json'), anchors=controller.reference_state())
                 for market, controller in controllers.items()}
    order_rows = [dict(id=o.id, market=o.market, side=o.side, price=str(o.price), amount=str(o.amount), filled=str(o.filled),
                       created_at=o.created_at, active_at=o.active_at, cancel_at=o.cancel_at, create_deadline=o.create_deadline) for o in orders]
    return dict(scenario=scenario, seed=seed, profile=profile, market_mode=market_mode, variant=variant, sensitivity=sensitivity,
                tape_sha256=tape_hash(tape), controller_class='controllers.market_making.life_mm.LifeMMController', ledger_verified=True,
                market_results=market_results, effective_controllers=effective,
                effective_config_sha256=hashlib.sha256(json.dumps(effective, sort_keys=True).encode()).hexdigest(),
                metrics=dict(fills=len(fills), volume_quote=str(volume), fees_quote=str(wallet.fees), funding_quote=str(wallet.funding),
                             initial_equity_quote=str(wallet.hold_equity(cfg.initial_mark)), final_equity_quote=str(final), hold_final_equity_quote=str(hold),
                             account_pnl_quote=str(final - wallet.hold_equity(cfg.initial_mark)), operating_cost_quote=str(operating),
                             net_pnl_after_assumed_cost_quote=str(final - wallet.hold_equity(cfg.initial_mark) - operating),
                             excess_over_hold_after_cost_quote=str(final - hold - operating), max_equity_drawdown_quote=str(drawdown),
                             ending_cash=str(wallet.cash), ending_spot_base=str(wallet.base), min_cash=str(min_cash), min_spot_base=str(min_base),
                             max_spot_base=str(max_base), peak_spot_outflow_with_reservations_quote=str(peak_outflow),
                             outflow_above_cap_observations=outflow_above_cap,
                             ending_perpetual_long_base=str(wallet.holds['BUY'].net_amount_base),
                             ending_perpetual_short_base=str(abs(wallet.holds['SELL'].net_amount_base)), peak_perpetual_gross_base=str(peak_gross),
                             minimum_initial_margin_headroom_quote=str(min_margin), observed_net_cost_quote=str(net_cost),
                             joint_quality_seconds=joint_quality, joint_quality_fraction=str(D(joint_quality) / cfg.duration_seconds),
                             cost_per_qualified_minute_quote=str(net_cost * 60 / joint_quality) if joint_quality else None,
                             fee_per_traded_quote=str(wallet.fees / volume) if volume else None,
                             cancels=cancellations, post_only_rejections=rejections, expired_placement_rejections=expired_placements,
                             observation_seconds=tape[-1]['at']),
                liquidity=stats, reasons=dict(reasons), markouts=markouts(fills, {row['at']: D(row['mark']) for row in tape}),
                raw=dict(tape=tape, fills=fills, recorder_shaped_fills=rows, funding_bills=exchange.funding_bills,
                         spot_buy_admissions=buy_admissions, orders=order_rows, equity=equity))

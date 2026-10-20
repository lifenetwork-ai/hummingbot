"""Native quote proposals and post-dispatch costs from original signed markets.

Imported configuration supplies explicit model coefficients, never live defaults.
Proposals/costs grant no capital, activation, runner membership or HTTP authority.
The owning service must still allocate, capture, dispatch and reconcile once.
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal as D, localcontext

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.economics import EconomicInputs, EconomicPolicy, evaluate_quote
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import _amount
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


@dataclass(frozen=True)
class ProducedQuoteCosts:
    subsidy_quote_usdt: D
    fee_buffer_quote_usdt: D
    fee_buffer_base_life: D
    carry_cost_usdt: D
    inventory_risk_usdt: D


class PgSpotQuoteProducer:
    @classmethod
    async def create(cls, source):
        if type(source) is not PgQuoteAccountSource or source._market_source is None:
            raise StorageError('PG_SPOT_PRODUCER_SOURCE_REQUIRED')
        source._credentials()
        p = cls()
        p.source, p.market, p.journal = source, source._market_source, source.journal
        p.runtime, p.binding = p.market.runtime, p.market.binding
        p._context = source, p.market, p.journal, p.runtime, p.binding
        p.application = None
        p._bound_application = None
        p._proposals, p._used = {}, set()
        p._busy, p._stopped = False, False
        body = await p._read_configuration()
        try:
            required = {
                'controller_id', 'config_version', 'levels', 'carry_cost_bps',
                'inventory_risk_bps', 'max_subsidy_per_quote_usdt',
            }
            if (type(body) is not dict or set(body) != required
                    or body['controller_id'] != p.journal.controller_id
                    or type(body['config_version']) is not int or body['config_version'] != p.journal.config_version
                    or type(body['levels']) is not list or not 0 < len(body['levels']) <= min(64, p.journal.max_actions)):
                raise ValueError
            levels = []
            for level in body['levels']:
                if type(level) is not dict or set(level) != {'spread_bps', 'quantity_base'}:
                    raise ValueError
                spread, size = _amount(level['spread_bps']), _amount(level['quantity_base'])
                if not 0 < spread < 10000 or size <= 0:
                    raise ValueError
                levels.append((spread, size))
            p._levels = tuple(levels)
            p._carry, p._inventory, p._subsidy = (_amount(body[key]) for key in (
                'carry_cost_bps', 'inventory_risk_bps', 'max_subsidy_per_quote_usdt'))
            if min(p._carry, p._inventory, p._subsidy) < 0 or max(p._carry, p._inventory) >= 10000:
                raise ValueError
            p._configuration_hash = fingerprint(body)
            p._model = p._levels, p._carry, p._inventory, p._subsidy
        except (KeyError, TypeError, ValueError, ArithmeticError, StorageError):
            raise StorageError('PG_SPOT_PRODUCER_CONFIG_INVALID') from None
        return p

    def _original(self):
        source, market, journal, runtime, binding = self._context
        source._credentials()
        market._original()
        if (self._stopped or self.source is not source or self.market is not market or self.journal is not journal
                or self.runtime is not runtime or self.binding != binding or source._market_source is not market
                or source.journal is not journal or journal.repository is not market.repository
                or self.application is not self._bound_application
                or hasattr(self, '_model') and self._model != (self._levels, self._carry, self._inventory, self._subsidy)):
            raise StorageError('PG_SPOT_PRODUCER_CONTEXT_CHANGED')

    async def _read_configuration(self):
        self._original()

        async def read(c):
            return await c.fetchval('''SELECT CASE WHEN octet_length(body::text)<=65536
                THEN body->'spot_quote_producer' END FROM life_financial.configurations
                WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3 AND config_version=$4''',
                                    self.runtime.config.account_id, self.binding.config_hash,
                                    self.binding.policy_hash, self.journal.config_version)
        body = await self.runtime._admit(read, safety=True)
        self._original()
        return body

    async def _configuration(self):
        if fingerprint(await self._read_configuration()) != self._configuration_hash:
            raise StorageError('PG_SPOT_PRODUCER_CONFIGURATION_CHANGED')

    def bind(self, application):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
        self._original()
        if (self.application is not None or type(application) is not PgSpotQuoteApplication
                or application.source is not self.source or application._prepare != self.prepare):
            raise StorageError('PG_SPOT_PRODUCER_APPLICATION_MISMATCH')
        application._original()
        self.application = self._bound_application = application

    async def propose(self, intent_id, side, level):
        """Plan one immutable intent before preallocation; never capture a V2 action."""
        self._original()
        identifier(intent_id)
        if intent_id in self._used:
            raise StorageError('PG_SPOT_PRODUCER_INTENT_USED')
        if (type(side) is not TradeType or side not in (TradeType.BUY, TradeType.SELL)
                or type(level) is not int or not 0 <= level < len(self._levels)):
            raise StorageError('PG_SPOT_PRODUCER_LEVEL_INVALID')
        if self._busy or len(self._used) >= self.journal.max_actions:
            raise StorageError('PG_SPOT_PRODUCER_CAPACITY')
        self._used.add(intent_id)
        self._busy = True
        try:
            await self._configuration()
            capture = await self.market.collect()
            self._original()
            spread, size = self._levels[level]
            with localcontext() as context:
                context.prec = 160
                tick, lot = capture.rules.tick_size, capture.rules.lot_size
                sign = D('-1') if side is TradeType.BUY else D('1')
                price = capture.reference.price_usdt * (1 + sign * spread / 10000)
                price = (price / tick).to_integral_value(rounding=ROUND_FLOOR if side is TradeType.BUY else ROUND_CEILING) * tick
                size = (size / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
            if size < capture.rules.min_size or price <= 0:
                raise StorageError('PG_SPOT_PRODUCER_BELOW_MINIMUM')
            intent = PgSpotQuoteIntent(intent_id, self.journal.controller_id, self.journal.owner,
                                       self.journal.config_version, side, level, size, price)
            # Record the exact originating intent before assessment. A refusal
            # consumes this local ID, without creating a native claim or wire.
            self._proposals[intent_id] = intent
            try:
                self.assess(capture, intent)
            except BaseException:
                self._proposals.pop(intent_id, None)
                raise
            return intent
        finally:
            self._busy = False

    def assess(self, capture, intent):
        self._original()
        if (type(intent) is not PgSpotQuoteIntent or self._proposals.get(intent.intent_id) is not intent):
            raise StorageError('PG_SPOT_PRODUCER_INTENT_ORIGIN_REQUIRED')
        with localcontext() as context:
            context.prec = 160
            # admission() verifies this exact process-local capture, original
            # account cut and original timestamps before any arithmetic result.
            facts = self.market.admission(capture, side=intent.side.name, quantity_base=intent.quantity_base,
                                          carry_cost_usdt=D('0'), inventory_risk_usdt=D('0'))
            if capture.report.producer_configuration_hash != self._configuration_hash:
                raise StorageError('PG_SPOT_PRODUCER_CONFIGURATION_CHANGED')
            account = capture.report.account
            inventory = account.base_life + account.long_base - account.short_base
            change = intent.quantity_base * (1 if intent.side is TradeType.BUY else -1)
            increase = max(D('0'), abs(inventory + change) - abs(inventory))
            carry = intent.quantity_base * facts.reference_price_usdt * self._carry / 10000
            risk = increase * facts.reference_price_usdt * self._inventory / 10000
            # The admitted PG schema contract fixes monetary scale at 18.
            # Prospective holds round UP; never rely on SQL numeric coercion.
            quantum = D('1e-18')
            carry, risk = (v.quantize(quantum, rounding=ROUND_CEILING) for v in (carry, risk))
            decision = evaluate_quote(EconomicInputs(
                intent.side.name, intent.price_usdt, intent.quantity_base, facts.exit_value_usdt,
                capture.rules.tick_size, capture.rules.lot_size, capture.rules.min_size,
                facts.maker_fee_rate, facts.exit_fee_rate, D('0'), carry, risk,
                self.market._policy.uncertainty_bps, True),
                EconomicPolicy(self.market._policy.objective, self.market._policy.min_net_edge_bps),
                subsidy_remaining_quote=self._subsidy)
            if (not decision.allowed or decision.final_price_usdt != intent.price_usdt
                    or decision.final_quantity_base != intent.quantity_base
                    or intent.side is TradeType.BUY and intent.price_usdt >= facts.best_ask_usdt
                    or intent.side is TradeType.SELL and intent.price_usdt <= facts.best_bid_usdt):
                raise StorageError('PG_SPOT_PRODUCER_ECONOMICS_DENIED')
            # Either possible fee currency is held. Never pre-credit a rebate.
            rate = max(D('0'), facts.maker_fee_rate)
            quote_fee = (intent.price_usdt * intent.quantity_base * rate).quantize(quantum, rounding=ROUND_CEILING)
            base_fee = (intent.quantity_base * rate).quantize(quantum, rounding=ROUND_CEILING)
            subsidy = decision.subsidy_reserved_quote.quantize(quantum, rounding=ROUND_CEILING)
            if subsidy > self._subsidy:
                raise StorageError('PG_SPOT_PRODUCER_ECONOMICS_DENIED')
            return ProducedQuoteCosts(subsidy, quote_fee, base_fee, carry, risk)

    async def prepare(self, action, wire):
        self._original()
        if self.application is None or self.application.preparing_action() != action:
            raise StorageError('PG_SPOT_PRODUCER_APPLICATION_MISMATCH')
        # The source brackets the immutable imported body in its original
        # before/after primary scope. assess() consumes that original seal;
        # no separate policy query is needed inside the quote-age window.
        return await self.market.prepare_produced(self.application, wire, self)

    def stop(self):
        self._stopped = True
        self.source.revoke()

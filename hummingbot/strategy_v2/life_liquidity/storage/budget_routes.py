"""Per-intent economic pools over the same financial authority and saga.

Quote availability never includes exit capacity. Routing derives solely from the
original immutable prepared command, not an ID prefix or a caller's later hint.
"""
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


class RoutedSubsidyLedger(SubsidyBudgetLedger):
    def __init__(self, store, quote, exit):
        self.store, self.quote, self.exit = store, quote, exit
        self._authorities = quote, exit
        for key in ('path', 'campaign_id', 'campaign_limit_quote', 'day_limit_quote', 'session_limit_quote'):
            setattr(self, key, getattr(quote, key))

    def _verify_binding(self):
        self.store.assert_worker_thread()
        if (self.store._subsidy is not self or (self.quote, self.exit) != self._authorities
                or (self.quote.path, self.exit.path) != (self.store._files.subsidy, self.store._files.exit_subsidy)):
            raise StorageError('SPOT_BUDGET_AUTHORITY_CHANGED')
        for native, policy in ((self.quote, self.store._subsidy_policy), (self.exit, self.store._exit_subsidy_policy)):
            if (native.campaign_id != policy.campaign_id
                    or native._policy() != {k: v for k, v in policy.json().items() if k != 'campaign_id'}):
                raise StorageError('SPOT_BUDGET_POLICY_CHANGED')

    def _policy(self):
        self._verify_binding()
        return self.quote._policy()

    def _route(self, intent_id):
        self._verify_binding()
        commands = self.store._read().resources['commands']
        scopes = {r['command'].get('budget_scope', 'QUOTE') for r in commands.values()
                  if r['command']['intent_id'] == intent_id}
        if len(scopes) != 1:
            raise StorageError('SPOT_BUDGET_ORIGINAL_COMMAND_REQUIRED')
        return self.exit if scopes == {'EXIT'} else self.quote

    def verified_status(self, **kwargs):
        self._verify_binding()
        return self.quote.verified_status(**kwargs)

    @property
    def campaign_committed_quote(self):
        self._verify_binding()
        return self.quote.campaign_committed_quote

    def verified_intent_ids(self):
        self._verify_binding()
        quote, exit = self.quote.verified_intent_ids(), self.exit.verified_intent_ids()
        if quote & exit or any(self._route(k) is not self.quote for k in quote) or any(self._route(k) is not self.exit for k in exit):
            raise StorageError('SPOT_BUDGET_SCOPE_CHANGED')
        return quote | exit

    def verified_fill_floors(self):
        self.verified_intent_ids()
        return self.quote.verified_fill_floors() | self.exit.verified_fill_floors()

    def verified_intent_sessions(self):
        self.verified_intent_ids()
        return self.quote.verified_intent_sessions() | self.exit.verified_intent_sessions()

    def verified_inventory_cycles(self):
        if self.exit.verified_inventory_cycles():
            raise StorageError('EXIT_BUDGET_FOREIGN_INVENTORY_CYCLE')
        return self.quote.verified_inventory_cycles()

    def _settle_inventory_cycle(self, cycle_id, *, proof, actuals):
        if any(self._route(k) is not self.quote for k in actuals):
            raise StorageError('EXIT_BUDGET_INVENTORY_CYCLE_FORBIDDEN')
        return self.quote._settle_inventory_cycle(cycle_id, proof=proof, actuals=actuals)


def _route_method(name):
    def forward(self, intent_id, *args, **kwargs):
        return getattr(self._route(intent_id), name)(intent_id, *args, **kwargs)
    return forward


for _method in ('reserve', 'reconcile', '_reconcile', 'record_fill_floor', 'matches_fill_floor', 'matches_settled',
                'matches_intent_session', 'matches_reservation', 'reserved_for', 'release_unsent', 'settle_zero_fill_terminal'):
    setattr(RoutedSubsidyLedger, _method, _route_method(_method))

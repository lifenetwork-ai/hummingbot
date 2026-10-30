"""Opt-in diagnostic wrappers; retain original quote decisions and actions."""
import json
import logging
import time
from decimal import Decimal

from controllers.market_making.life_mm import LifeMMController


def install():
    original_valid = LifeMMController.spread_quote_valid
    original_refresh = LifeMMController._defense_refresh_actions
    original_stop = LifeMMController.stop_quote_actions
    remaining = 2000

    def emit(controller, kind, **details):
        nonlocal remaining
        if remaining <= 0:
            return
        remaining -= 1
        row = dict(at=time.time(), kind=kind, controller=controller.config.id,
                   session_id=controller.config.mm_session_id,
                   ready=controller.processed_data.get('ready'),
                   session_admitted=controller._session_admitted,
                   provider_ready=controller.market_data_provider.ready,
                   control_error=controller._control_error,
                   quote_diagnostics=controller.quote_diagnostics,
                   reason=controller.processed_data.get('reason'),
                   guard=controller.processed_data.get('quote_guard'), **details)
        logging.getLogger('handover_probe').info('HANDOVER_PROBE %s', json.dumps(row, default=str))

    def valid(self, side, price, amount, *, level_id=None):
        result = original_valid(self, side, price, amount, level_id=level_id)
        if (not result and self.processed_data.get('ready') and self._spread_policy.defense is not None
                and level_id in self.get_not_active_levels_ids([])):
            reference = self.processed_data['reference_price']
            target_price, target_amount = self.get_price_and_amount(level_id)
            spreads = self._effective_spreads[0 if side.name == 'BUY' else 1]
            index = int(self.get_level_from_level_id(level_id))
            actual = (reference - price if side.name == 'BUY' else price - reference) / reference
            emit(self, 'invalid_spread_quote', level=level_id, side=side.name,
                 price=price, amount=amount, reference=reference, actual_spread=actual,
                 minimum_spread=spreads[index], target_price=target_price, target_amount=target_amount,
                 price_too_close=actual < Decimal(str(spreads[index])),
                 notional_too_large=amount * price > target_price * target_amount,
                 defense_state=self._spread_policy.defense.state)
        return result

    def refresh(self):
        actions = original_refresh(self)
        if actions or not getattr(self, '_handover_probe_observed', False):
            emit(self, 'refresh_cancel' if actions else 'refresh_checked',
                 executor_ids=[a.executor_id for a in actions])
            self._handover_probe_observed = True
        return actions

    def stop(self):
        actions = original_stop(self)
        if actions:
            emit(self, 'guard_cancel', executor_ids=[a.executor_id for a in actions])
        return actions

    LifeMMController.spread_quote_valid = valid
    LifeMMController._defense_refresh_actions = refresh
    LifeMMController.stop_quote_actions = stop

/** Synthetic typed configuration for component tests; never import into runtime pages. */
import type { components } from '../../core/api/schema'

export const marketConfig: components['schemas']['LifeMMConfigOutput'] = {
  "quote_refresh_max_delay_seconds": 0,
  "id": "life_spot",
  "controller_name": "life_mm",
  "controller_type": "market_making",
  "total_amount_quote": "100",
  "manual_kill_switch": false,
  "initial_positions": [],
  "connector_name": "okx",
  "trading_pair": "LIFE-USDT",
  "buy_spreads": [
    0.01,
    0.02
  ],
  "sell_spreads": [
    0.01,
    0.02
  ],
  "buy_amounts_pct": [
    "1",
    "1"
  ],
  "sell_amounts_pct": [
    "1",
    "1"
  ],
  "executor_refresh_time": 60,
  "cooldown_time": 15,
  "leverage": 1,
  "position_mode": "HEDGE",
  "stop_loss": null,
  "take_profit": null,
  "time_limit": null,
  "take_profit_order_type": 2,
  "trailing_stop": null,
  "position_rebalance_threshold_pct": "0.05",
  "skip_rebalance": true,
  "reference_connector": "okx",
  "scenario": "sideways",
  "references": [],
  "max_reference_pairs": 8,
  "max_data_age_seconds": "10",
  "initial_anchor": null,
  "min_price": "0.000001",
  "max_price": "1000",
  "margin_mode": "CROSS",
  "max_base_inventory": "10000",
  "max_long_base": null,
  "max_short_base": null,
  "max_gross_base": null,
  "runtime_config_managed": false,
  "mm_session_id": null,
  "mm_generation": null,
  "saved_life_anchor": null,
  "saved_source_anchors": {},
  "quote_guards": {
    "enabled": false,
    "mode": "strict",
    "bootstrap": null,
    "max_deviation_bps": null,
    "resume_deviation_bps": null,
    "recovery_delay_seconds": "5",
    "max_book_levels": 50,
    "max_own_orders": 200
  },
  "sideways_range": null,
  "inventory_skew": {
    "enabled": false,
    "target_base_pct": "0.5",
    "inventory_range_multiplier": "1"
  },
  "spread_policy": {
    "fee_floor": false,
    "volatility": {
      "defense": null,
      "enabled": false,
      "interval": "1m",
      "length": 14,
      "target_natr": "0.01",
      "min_multiplier": "0.5",
      "max_multiplier": "3",
      "max_age_seconds": 120,
      "on_unavailable": "wait",
      "fallback_buy_spreads": null,
      "fallback_sell_spreads": null
    }
  }
}

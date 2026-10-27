"""Stable readiness reason codes shared by the MM controller and service."""

MM_READINESS_REASONS = frozenset({"perpetual_account_not_confirmed", "reference_pair_unavailable", "stale_reference",
                                  "invalid_book", "reference_out_of_bounds", "quote_out_of_bounds", "market_price_unavailable",
                                  "reference_deviation", "sideways_breakout", "guard_recovering",
                                  "bootstrap_binding_unavailable", "bootstrap_not_started", "bootstrap_checkpoint_pending",
                                  "bootstrap_expired", "bootstrap_band", "bootstrap_range", "bootstrap_quote_crossing",
                                  "market_liquidity_unqualified", "bootstrap_anchor_mismatch", "account_balance_unavailable",
                                  "fee_basis_unavailable", "candles_unavailable"})

import type { components } from '../../core/api/schema'

type Repair = components['schemas']['RecoveryPositionRepairOutput']
const fields = [
  ['side', 'Side', 'side'], ['amount', 'Amount', 'base'], ['breakeven_price', 'Breakeven Price', 'quote_per_base'],
  ['cum_fees_quote', 'Cumulative Fees', 'quote'], ['realized_pnl_quote', 'Realized PnL', 'quote'],
  ['unrealized_pnl_quote', 'Unrealized PnL', 'quote'], ['volume_traded_quote', 'Traded Volume', 'quote'],
] as const
/** Complete ledger comparison only; live exchange PnL stays in the positions source. */
export function buildRecoveryComparison(changes: readonly Repair[] = []) {
  return changes.map((change) => ({ id: change.id, pair: change.pair, connector: change.connector,
    controllerId: change.controller_id, valuationBasis: change.valuation_basis,
    rows: fields.map(([field, label, unit]) => ({ field, label, unit, before: change.before[field], after: change.after[field] })),
    explanation: 'Values are reconstructed from checkpoint, not live PnL. A reconstructed PnL of 0 does not imply a loss refund; inspect exchange positions independently.',
  }))
}
export type RecoveryComparisonModel = ReturnType<typeof buildRecoveryComparison>

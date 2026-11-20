import type { components } from '../../core/api/schema'

export type UncertainOrderRepair = components['schemas']['RecoveryUncertainOrderRepairOutput']

export function validateUncertainOrderIds(ids: readonly string[]): string | null {
  if (ids.length === 0) return null
  if (ids.some((id) => !/^[a-zA-Z0-9]{1,32}$/.test(id))) return 'Order IDs must contain 1–32 letters or numbers.'
  if (new Set(ids).size !== ids.length) return 'Order IDs must be distinct.'
  if (ids.length !== 1 && (ids.length < 2 || ids.length > 8)) return 'Review one order or a batch of 2–8 orders.'
  return null
}

export function normalizeUncertainOrderRepairs(operation: Pick<components['schemas']['RecoveryOperationOutput'],
  'proposed_uncertain_order' | 'proposed_uncertain_orders'> | undefined): UncertainOrderRepair[] {
  if (!operation) return []
  if (operation.proposed_uncertain_orders?.length) return operation.proposed_uncertain_orders
  return operation.proposed_uncertain_order ? [operation.proposed_uncertain_order] : []
}

export function exactUncertainOrderRepairs(ids: readonly string[], repairs: readonly UncertainOrderRepair[]): boolean {
  return ids.length > 0 && repairs.length === ids.length &&
    [...repairs].map(({ id }) => id).sort().join('\0') === [...ids].sort().join('\0')
}

export function uncertainOrderQuery(ids: readonly string[]) {
  if (ids.length === 1) return { query: { uncertain_order_id: ids[0] } }
  if (ids.length > 1) return { query: { uncertain_order_ids: ids.join(',') } }
  return {}
}

export function uncertainOrderRequest(ids: readonly string[]) {
  if (ids.length === 1) return { uncertain_order_id: ids[0] }
  if (ids.length > 1) return { uncertain_order_ids: [...ids] }
  return {}
}

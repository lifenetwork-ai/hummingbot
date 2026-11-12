/** Display-only decimal formatting. Server decimal strings remain authoritative. */
type Decimal = { negative: boolean; digits: string; scale: number }
function decimal(value: string | number | null | undefined): Decimal | null {
  if (value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return null
  const raw = String(value).trim()
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(raw)
  if (!match || (!match[2] && !match[3])) return null
  const exponent = Number(match[4] ?? 0)
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 10000) return null
  let digits = (match[2] + (match[3] ?? '')).replace(/^0+(?=\d)/, '')
  let scale = (match[3]?.length ?? 0) - exponent
  if (scale < 0) { digits += '0'.repeat(-scale); scale = 0 }
  return { negative: match[1] === '-' && /[1-9]/.test(digits), digits, scale }
}
function group(integer: string) { return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') }
function render(value: Decimal): string {
  const padded = value.digits.padStart(value.scale + 1, '0')
  const integer = value.scale ? padded.slice(0, -value.scale) : padded
  const fraction = value.scale ? `.${padded.slice(-value.scale)}` : ''
  return `${value.negative ? '-' : ''}${group(integer)}${fraction}`
}
function fixed(value: Decimal | null, places: number): string {
  if (!value) return '—'
  const amount = BigInt(value.digits)
  let rounded: bigint
  if (value.scale > places) {
    const divisor = 10n ** BigInt(value.scale - places)
    rounded = amount / divisor + (amount % divisor * 2n >= divisor ? 1n : 0n)
  } else rounded = amount * 10n ** BigInt(places - value.scale)
  if (amount !== 0n && rounded === 0n) {
    const unit = places ? `0.${'0'.repeat(places - 1)}1` : '1'
    return value.negative ? `>-${unit}` : `<${unit}`
  }
  return render({ negative: value.negative && rounded !== 0n, digits: rounded.toString(), scale: places })
}
/** Shared display rounding convention: default 4 decimal places across the app. */
export const DEFAULT_DISPLAY_PLACES = 4

/**
 * Generic display-only rounding. Server decimal strings remain authoritative;
 * this only affects what the operator sees. Default 4 places.
 */
export function formatAmount(value: string | number | null | undefined, places: number = DEFAULT_DISPLAY_PLACES): string {
  return fixed(decimal(value), places)
}
export function formatUsdt(value: string | number | null | undefined): string {
  const output = fixed(decimal(value), 2)
  return output === '—' ? output : `${output} USDT`
}
export function formatLife(value: string | number | null | undefined): string {
  const output = fixed(decimal(value), 4)
  return output === '—' ? output : `${output} LIFE`
}
export function formatPrice(value: string | number | null | undefined, places: number = DEFAULT_DISPLAY_PLACES): string {
  return formatAmount(value, places)
}
export function formatAssetAmount(value: string | number | null | undefined, currency?: string): string {
  const parsed = decimal(value)
  const curr = currency?.toUpperCase()
  if (!parsed) return '—'
  const places = curr === 'USDT' || curr === 'USD' ? 2 : curr === 'BTC' || curr === 'ETH' || curr === 'LIFE' ? 4 : 2
  const output = fixed(parsed, places)
  if (output === '—') return '—'
  return curr ? `${output} ${curr}` : output
}
export function localizeSystemMessage(text: string | null | undefined): string {
  if (!text) return ''
  return text.trim()
}
export function localizePhase(phase: string | null | undefined): string {
  if (!phase) return 'Unknown'
  const p = phase.toLowerCase()
  if (p === 'starting') return 'Starting'
  if (p === 'running') return 'Running'
  if (p === 'stopping') return 'Stopping'
  if (p === 'stopped') return 'Stopped'
  if (p === 'error') return 'Error'
  return phase.charAt(0).toUpperCase() + phase.slice(1)
}
export function formatSpread(value: string | number | null | undefined): string {
  const output = formatAmount(value)
  return output === '—' ? output : `${output} bps`
}
export function formatPercentage(value: number | string | null | undefined, isFraction = true): string {
  const parsed = decimal(value)
  if (parsed && isFraction) {
    if (parsed.scale >= 2) parsed.scale -= 2
    else { parsed.digits += '0'.repeat(2 - parsed.scale); parsed.scale = 0 }
  }
  const output = fixed(parsed, 1)
  return output === '—' ? output : `${output}%`
}
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) return '—'
  const hrs = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  const tail = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
  return hrs > 0 ? `${hrs.toString().padStart(2, '0')}:${tail}` : tail
}

export function formatSpreadValue(val: number | string): string {
  const parsed = decimal(val)
  if (!parsed) return '—'
  // Spread configuration is a fraction: 1 bps = 0.0001.
  const bps = { ...parsed }
  if (bps.scale >= 4) bps.scale -= 4
  else { bps.digits += '0'.repeat(4 - bps.scale); bps.scale = 0 }
  const percent = { ...bps, scale: bps.scale + 2 }
  const percentage = fixed(percent, 2).replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '')
  return `${fixed(bps, DEFAULT_DISPLAY_PLACES)} bps (${percentage}%)`
}

export function formatSpreadList(spreads: (number | string)[] | null | undefined): string {
  if (!spreads || spreads.length === 0) return '—'
  return spreads.map(formatSpreadValue).join(', ')
}

export function formatTechnicalNumber(val: number | string | null | undefined): string {
  if (val === null || val === undefined || val === '') return '—'
  const str = String(val).trim()
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(str)
  if (match && match[4]) {
    const exp = Number(match[4])
    if (exp < -10) return '0'
    if (exp > 10) return str
    const num = Number(str)
    if (!isNaN(num)) {
      return num.toFixed(Math.min(8, Math.max(0, -exp + (match[3]?.length ?? 0)))).replace(/\.?0+$/, '')
    }
  }
  return str
}

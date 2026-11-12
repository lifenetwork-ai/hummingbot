/** Exact arithmetic on authoritative plain decimal strings; null never becomes zero. */
export function sumDecimalStrings(values: readonly (string | null | undefined)[]): string | null {
  if (!values.length) return null
  const parts = values.map((value) => typeof value === 'string' ? /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(value) : null)
  if (parts.some((part) => !part)) return null
  const scale = Math.max(...parts.map((part) => part?.[3]?.length ?? 0))
  let sum = 0n
  for (const part of parts) {
    if (!part) return null
    sum += (part[1] === '-' ? -1n : 1n) * BigInt(part[2] + (part[3] ?? '').padEnd(scale, '0'))
  }
  const digits = (sum < 0n ? -sum : sum).toString().padStart(scale + 1, '0')
  return `${sum < 0n ? '-' : ''}${scale ? `${digits.slice(0, -scale)}.${digits.slice(-scale)}` : digits}`
}
export function subtractDecimalStrings(left: string | null, right: string | null): string | null {
  return sumDecimalStrings([left, right === null ? null : right.startsWith('-') ? right.slice(1) : `-${right.replace(/^\+/, '')}`])
}

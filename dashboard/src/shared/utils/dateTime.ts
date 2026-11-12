export function toDateTimeLocalString(iso?: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  const year = date.getFullYear()
  const month = pad(date.getMonth() + 1)
  const day = pad(date.getDate())
  const hours = pad(date.getHours())
  const minutes = pad(date.getMinutes())
  return `${year}-${month}-${day}T${hours}:${minutes}`
}

export function fromDateTimeLocalString(localStr?: string | null): string | undefined {
  if (!localStr || !localStr.trim()) return undefined
  const date = new Date(localStr)
  if (isNaN(date.getTime())) return undefined
  return date.toISOString()
}

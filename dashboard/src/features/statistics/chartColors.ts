/** Fixed slot count; further series are not charted (listed in the table instead). */
export const MAX_CHART_SERIES = 8

const SERIES_COLOR_VARS = [
  '--chart-series-1',
  '--chart-series-2',
  '--chart-series-3',
  '--chart-series-4',
  '--chart-series-5',
  '--chart-series-6',
  '--chart-series-7',
  '--chart-series-8',
] as const

/**
 * Root class that defines the categorical series palette as CSS variables for
 * both themes (see `.chart-series-palette` in `src/styles.css`, which documents
 * the palette's validation and is its single source). Slot order is fixed and
 * assigned by series order, never cycled.
 */
export const SERIES_PALETTE_ROOT = 'chart-series-palette'

export function chartSeriesColor(slot: number): string {
  return `var(${SERIES_COLOR_VARS[slot] ?? SERIES_COLOR_VARS[SERIES_COLOR_VARS.length - 1]})`
}

import { describe, expect, it } from 'vitest'
import { safeRedirect } from './redirect'

describe('login return destination', () => {
  it.each(['/ui-kit', '/?market=spot#orders'])('preserves %s', (path) => expect(safeRedirect(path)).toBe(path))
  it.each([undefined, '//outside.test', 'https://outside.test', '/\\outside.test', '/login', '/login?redirect=/ui-kit', '/x/../login', '/\noutside.test'])('rejects unsafe or looping destination %s', (path) => expect(safeRedirect(path)).toBe('/'))
})

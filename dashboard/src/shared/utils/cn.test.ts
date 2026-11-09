import { describe, expect, it } from 'vitest'
import { cn } from './cn'

describe('cn', () => {
  it('merges conditional classes and resolves conflicting Tailwind utilities', () => {
    expect(cn('p-2', false, ['p-4', 'text-sm'], { 'font-bold': true })).toBe('p-4 text-sm font-bold')
  })
})

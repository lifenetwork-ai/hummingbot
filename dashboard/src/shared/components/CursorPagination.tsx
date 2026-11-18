import { ChevronRight } from 'lucide-react'
import { Button } from './Button'

/**
 * Shared cursor ("Load more") pagination for any endpoint that returns `next_cursor`.
 * The button renders only while more pages remain; absence means everything is loaded.
 */
export interface CursorPaginationProps {
  /** Singular item label, e.g. "command"; plural defaults to `${itemLabel}s`. */
  itemLabel: string
  /** Override for irregular plurals. */
  pluralLabel?: string
  hasMore?: boolean
  isLoading?: boolean
  onLoadMore?: () => void
}

export function CursorPagination({ itemLabel, pluralLabel, hasMore, isLoading, onLoadMore }: CursorPaginationProps) {
  const plural = pluralLabel ?? `${itemLabel}s`
  if (!hasMore || !onLoadMore) return null
  return (
    <div className="flex justify-center">
      <Button
        variant="secondary"
        onClick={onLoadMore}
        disabled={isLoading}
        className="h-8 px-4 text-xs gap-1"
      >
        <span>{isLoading ? 'Loading more…' : `Load more ${plural}`}</span>
        <ChevronRight size={14} />
      </Button>
    </div>
  )
}

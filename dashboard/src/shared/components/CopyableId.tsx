import { cn } from '../utils/cn'
import { CopyButton } from './CopyButton'

export interface CopyableIdProps {
  id: string
  truncateLength?: number
  className?: string
  copyLabel?: string
  codeClassName?: string
}

export function CopyableId({
  id,
  truncateLength,
  className,
  copyLabel = 'Copy',
  codeClassName,
}: CopyableIdProps) {
  if (!id) return null

  const isTruncated = Boolean(truncateLength && id.length > truncateLength)
  const displayText = isTruncated ? `${id.slice(0, truncateLength)}…` : id

  return (
    <span
      className={cn('inline-flex items-center gap-1 max-w-full group/copy', className)}
      title={id}
    >
      <code
        className={cn(
          'font-mono text-app-text select-all cursor-text text-xs',
          isTruncated ? 'truncate' : 'break-all',
          codeClassName
        )}
      >
        {displayText}
      </code>
      <CopyButton
        text={id}
        label={copyLabel}
        size={11}
        className="opacity-70 group-hover/copy:opacity-100 transition-opacity shrink-0"
      />
    </span>
  )
}

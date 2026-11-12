import { X } from 'lucide-react'
import {
  type PropsWithChildren,
  type ReactNode,
  useEffect,
  useId,
  useRef,
} from 'react'
import { createPortal } from 'react-dom'
import { cn } from '../utils/cn'

type DialogProps = PropsWithChildren<{
  open: boolean
  onClose: () => void
  title: string
  description?: string
  footer?: ReactNode
  className?: string
  placement?: 'center' | 'right'
}>

export function Dialog({
  open,
  onClose,
  title,
  description,
  footer,
  className,
  placement = 'center',
  children,
}: DialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return

    const opener = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null

    const focusable = () => Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), summary, input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ) ?? [],
    )

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
      }
      if (event.key === 'Tab') {
        const items = focusable()
        if (items.length === 0) {
          event.preventDefault()
          dialogRef.current?.focus()
          return
        }
        const first = items[0]
        const last = items[items.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first.focus()
        }
      }
    }

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', handleKeyDown)
    ;(focusable()[0] ?? dialogRef.current)?.focus()

    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleKeyDown)
      opener?.focus()
    }
  }, [onClose, open])

  if (!open) return null

  return createPortal(
    <div className={cn('fixed inset-0 z-[100] grid p-4', placement === 'right' ? 'justify-items-end p-0' : 'place-items-center')}>
      <button
        type="button"
        aria-label="Close dialog"
        className="absolute inset-0 bg-black/35 backdrop-blur-sm"
        onClick={onClose}
      />

      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        className={cn(
          'relative z-10 w-full max-w-lg rounded-card border border-app-border bg-app-surface p-6 shadow-card md:p-7',
          placement === 'right' && 'min-h-0 h-full max-h-dvh max-w-xl overflow-y-auto rounded-none border-y-0 border-r-0 p-4 shadow-2xl sm:p-6',
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2
              id={titleId}
              className="font-heavy text-2xl font-extrabold tracking-[-0.035em]"
            >
              {title}
            </h2>
            {description ? (
              <p
                id={descriptionId}
                className="mt-2 text-sm leading-6 text-app-muted"
              >
                {description}
              </p>
            ) : null}
          </div>

          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-button text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-text"
          >
            <X size={17} />
          </button>
        </div>

        <div className="mt-6">{children}</div>

        {footer ? (
          <div className="mt-7 flex flex-wrap justify-end gap-3 border-t border-app-border pt-5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}

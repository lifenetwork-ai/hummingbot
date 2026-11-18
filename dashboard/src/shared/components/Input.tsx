import { forwardRef, useId, type InputHTMLAttributes } from 'react'
import { cn } from '../utils/cn'

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  /** Optional to mirror Select, for rows that render a custom label (e.g. with an inline action). */
  label?: string
  hint?: string
  error?: string
  /** Mirrors Select sizeVariant so mixed form rows keep a uniform control height. */
  sizeVariant?: 'sm' | 'md'
  containerClassName?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, id, className, containerClassName, sizeVariant = 'md', ...props }, ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = `${inputId}-hint`
  return (
    <div className={cn('grid', sizeVariant === 'sm' ? 'gap-1.5' : 'gap-2', containerClassName)}>
      {label && (
        <label
          htmlFor={inputId}
          className={cn(
            'font-semibold text-app-text',
            sizeVariant === 'sm' ? 'text-xs' : 'text-sm',
          )}
        >
          {label}
        </label>
      )}
      <input
        {...props}
        id={inputId}
        ref={ref}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? hintId : undefined}
        className={cn(
          'w-full rounded-button border border-app-border bg-app-surface text-app-text outline-none transition-colors placeholder:text-app-muted/70 focus:border-app-action focus:ring-2 focus:ring-app-action/15 disabled:cursor-not-allowed disabled:opacity-50',
          sizeVariant === 'sm' ? 'h-8 px-2.5 text-xs' : 'h-11 px-3 text-sm',
          error && 'border-app-danger focus:border-app-danger focus:ring-app-danger/15',
          className,
        )}
      />
      {error ? (
        <p id={hintId} className="text-xs text-app-danger">{error}</p>
      ) : hint ? (
        <p id={hintId} className="text-xs text-app-muted">{hint}</p>
      ) : null}
    </div>
  )
})

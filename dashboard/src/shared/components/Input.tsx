import { forwardRef, useId, type InputHTMLAttributes } from 'react'
import { cn } from '../utils/cn'

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label: string
  hint?: string
  error?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, hint, error, id, className, ...props }, ref,
) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = `${inputId}-hint`
  return (
    <div className="grid gap-2">
      <label htmlFor={inputId} className="text-sm font-semibold text-app-text">{label}</label>
      <input
        {...props}
        id={inputId}
        ref={ref}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? hintId : undefined}
        className={cn(
          'h-11 w-full rounded-button border border-app-border bg-app-surface px-3 text-sm text-app-text outline-none transition-colors placeholder:text-app-muted/70 focus:border-app-action focus:ring-2 focus:ring-app-action/15 disabled:cursor-not-allowed disabled:opacity-50',
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

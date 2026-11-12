import { forwardRef, useId, type SelectHTMLAttributes } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '../utils/cn'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string
  hint?: string
  error?: string
  sizeVariant?: 'sm' | 'md'
  options?: SelectOption[]
  containerClassName?: string
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  {
    label,
    hint,
    error,
    id,
    className,
    containerClassName,
    sizeVariant = 'md',
    options,
    children,
    disabled,
    ...props
  },
  ref
) {
  const generatedId = useId()
  const selectId = id ?? generatedId
  const hintId = `${selectId}-hint`

  const selectElement = (
    <div className={cn('relative w-full', !label && !hint && !error && containerClassName)}>
      <select
        {...props}
        id={selectId}
        ref={ref}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        aria-describedby={hint || error ? hintId : undefined}
        className={cn(
          'w-full appearance-none rounded-button border border-app-border bg-app-surface text-app-text outline-none transition-colors cursor-pointer',
          'focus:border-app-action focus:ring-2 focus:ring-app-action/15',
          'disabled:cursor-not-allowed disabled:opacity-50',
          sizeVariant === 'sm' ? 'h-8 pl-2.5 pr-8 text-xs' : 'h-11 pl-3 pr-10 text-sm',
          error && 'border-app-danger focus:border-app-danger focus:ring-app-danger/15',
          className
        )}
      >
        {options
          ? options.map((opt) => (
              <option
                key={opt.value}
                value={opt.value}
                disabled={opt.disabled}
                className="bg-app-surface text-app-text"
              >
                {opt.label}
              </option>
            ))
          : children}
      </select>
      <ChevronDown
        size={sizeVariant === 'sm' ? 14 : 16}
        className={cn(
          'pointer-events-none absolute top-1/2 -translate-y-1/2 text-app-muted transition-colors',
          sizeVariant === 'sm' ? 'right-2.5' : 'right-3',
          disabled && 'opacity-50'
        )}
        aria-hidden="true"
      />
    </div>
  )

  if (!label && !hint && !error) {
    return selectElement
  }

  return (
    <div className={cn('grid gap-1.5', containerClassName)}>
      {label && (
        <label
          htmlFor={selectId}
          className={cn(
            'font-semibold text-app-text',
            sizeVariant === 'sm' ? 'text-xs' : 'text-sm'
          )}
        >
          {label}
        </label>
      )}
      {selectElement}
      {error ? (
        <p id={hintId} className="text-xs text-app-danger">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className="text-xs text-app-muted">
          {hint}
        </p>
      ) : null}
    </div>
  )
})

import { forwardRef, useId, useRef, type InputHTMLAttributes } from 'react'
import { Calendar, X } from 'lucide-react'
import { cn } from '../utils/cn'
import { fromDateTimeLocalString, toDateTimeLocalString } from '../utils/dateTime'

export interface DateTimeInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange'> {
  label: string
  /** ISO 8601 UTC string (e.g. 2026-11-10T10:00:00Z) or empty string */
  value?: string | null
  /** Callback with ISO 8601 UTC string (or undefined when cleared) */
  onChange: (isoString: string | undefined) => void
  hint?: string
  error?: string
  sizeVariant?: 'sm' | 'md'
  containerClassName?: string
  clearable?: boolean
}

export const DateTimeInput = forwardRef<HTMLInputElement, DateTimeInputProps>(function DateTimeInput(
  {
    label,
    value,
    onChange,
    hint,
    error,
    id,
    className,
    containerClassName,
    sizeVariant = 'sm',
    clearable = true,
    disabled,
    placeholder = '2026-11-10T00:00:00Z',
    ...props
  },
  ref
) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const hintId = `${inputId}-hint`
  const pickerRef = useRef<HTMLInputElement>(null)

  const handleOpenPicker = () => {
    if (disabled) return
    try {
      if (typeof pickerRef.current?.showPicker === 'function') {
        pickerRef.current.showPicker()
      } else {
        pickerRef.current?.focus()
      }
    } catch {
      pickerRef.current?.focus()
    }
  }

  const handlePickerChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const nextIso = fromDateTimeLocalString(e.target.value)
    if (nextIso) {
      onChange(nextIso)
    }
  }

  const handleTextChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    onChange(e.target.value || undefined)
  }

  const handleClear = () => {
    onChange(undefined)
  }

  return (
    <div className={cn('grid gap-1.5', containerClassName)}>
      <div className="flex items-center justify-between">
        <label
          htmlFor={inputId}
          className={cn(
            'font-semibold text-app-text',
            sizeVariant === 'sm' ? 'text-xs' : 'text-sm'
          )}
        >
          {label}
        </label>
        {clearable && Boolean(value) && !disabled && (
          <button
            type="button"
            onClick={handleClear}
            className="text-[10px] text-app-muted hover:text-app-action transition-colors flex items-center gap-0.5"
            title={`Clear ${label}`}
          >
            <X size={10} />
            <span>Clear</span>
          </button>
        )}
      </div>

      <div className="relative flex items-center w-full">
        <input
          {...props}
          type="text"
          id={inputId}
          ref={ref}
          value={value ?? ''}
          disabled={disabled}
          placeholder={placeholder}
          onChange={handleTextChange}
          aria-invalid={Boolean(error)}
          aria-describedby={hint || error ? hintId : undefined}
          className={cn(
            'w-full rounded-button border border-app-border bg-app-surface text-app-text font-mono outline-none transition-colors',
            'focus:border-app-action focus:ring-2 focus:ring-app-action/15',
            'disabled:cursor-not-allowed disabled:opacity-50',
            sizeVariant === 'sm' ? 'h-8 pl-2.5 pr-8 text-xs' : 'h-11 pl-3 pr-10 text-sm',
            error && 'border-app-danger focus:border-app-danger focus:ring-app-danger/15',
            className
          )}
        />

        {/* Hidden native datetime picker for browser UI picker */}
        <input
          type="datetime-local"
          ref={pickerRef}
          aria-hidden="true"
          tabIndex={-1}
          disabled={disabled}
          value={toDateTimeLocalString(value)}
          onChange={handlePickerChange}
          className="sr-only pointer-events-none absolute -bottom-1 left-0 opacity-0"
        />

        <button
          type="button"
          onClick={handleOpenPicker}
          disabled={disabled}
          title="Select date & time (UI Picker)"
          aria-label="Open date & time picker"
          className={cn(
            'absolute right-1.5 flex items-center justify-center rounded p-1 text-app-muted hover:bg-app-surface-muted hover:text-app-action transition-colors',
            disabled && 'opacity-50 cursor-not-allowed'
          )}
        >
          <Calendar size={sizeVariant === 'sm' ? 14 : 16} />
        </button>
      </div>

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

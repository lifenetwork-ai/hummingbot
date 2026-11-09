import { cn } from '../utils/cn'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

const variants: Record<ButtonVariant, string> = {
  primary:
    'bg-app-action text-white hover:bg-app-action-hover hover:shadow-card active:translate-y-px',
  secondary:
    'border border-app-border bg-app-surface text-app-text hover:border-app-action hover:text-app-action',
  ghost:
    'bg-transparent text-app-muted hover:bg-app-surface-muted hover:text-app-text',
  danger:
    'border border-app-danger/40 bg-transparent text-app-danger hover:bg-app-danger hover:text-white',
}

export function buttonClassName(variant: ButtonVariant = 'primary', className?: string) {
  return cn(
    'inline-flex h-11 items-center justify-center gap-2 rounded-button px-5 font-semibold transition-[transform,background-color,border-color,color,box-shadow] duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action focus-visible:ring-offset-2 focus-visible:ring-offset-app-bg disabled:cursor-not-allowed disabled:opacity-45',
    variants[variant],
    className,
  )
}


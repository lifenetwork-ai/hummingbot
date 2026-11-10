import type { ReactNode } from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '../utils/cn'

export type BannerVariant = 'info' | 'warning' | 'error' | 'success'

export type BannerAction = {
  label: string
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void
}

export type BannerProps = {
  variant?: BannerVariant
  title: string
  description?: ReactNode
  action?: BannerAction | ReactNode
  onDismiss?: () => void
  className?: string
}

function isBannerAction(action: BannerAction | ReactNode): action is BannerAction {
  return (
    typeof action === 'object' &&
    action !== null &&
    'label' in action &&
    typeof (action as BannerAction).onClick === 'function'
  )
}

const variantStyles: Record<BannerVariant, {
  container: string
  iconColor: string
  titleColor: string
  descriptionColor: string
  Icon: typeof Info
}> = {
  info: {
    container: 'border-app-action/25 bg-app-action/10 text-app-action',
    iconColor: 'text-app-action',
    titleColor: 'text-app-text',
    descriptionColor: 'text-app-muted',
    Icon: Info,
  },
  warning: {
    container: 'border-app-warning/25 bg-app-warning/10 text-app-warning',
    iconColor: 'text-app-warning',
    titleColor: 'text-app-text',
    descriptionColor: 'text-app-muted',
    Icon: AlertTriangle,
  },
  error: {
    container: 'border-app-danger/25 bg-app-danger/10 text-app-danger',
    iconColor: 'text-app-danger',
    titleColor: 'text-app-danger',
    descriptionColor: 'text-app-danger/80',
    Icon: AlertCircle,
  },
  success: {
    container: 'border-app-success/25 bg-app-success/10 text-app-success',
    iconColor: 'text-app-success',
    titleColor: 'text-app-text',
    descriptionColor: 'text-app-muted',
    Icon: CheckCircle2,
  },
}

export function Banner({
  variant = 'info',
  title,
  description,
  action,
  onDismiss,
  className,
}: BannerProps) {
  const { container, iconColor, titleColor, descriptionColor, Icon } = variantStyles[variant]
  const role = variant === 'error' ? 'alert' : 'status'

  return (
    <div
      role={role}
      className={cn(
        'flex w-full items-start justify-between gap-3 rounded-card border p-4 text-sm transition-colors',
        container,
        className,
      )}
    >
      <div className="flex flex-1 items-start gap-3">
        <Icon className={cn('mt-0.5 h-4 w-4 shrink-0', iconColor)} aria-hidden="true" />
        <div className="flex-1 space-y-1">
          <p className={cn('font-semibold leading-tight', titleColor)}>{title}</p>
          {description && (
            <div className={cn('text-xs leading-relaxed', descriptionColor)}>
              {description}
            </div>
          )}
          {action && (
            <div className="pt-2">
              {isBannerAction(action) ? (
                <button
                  type="button"
                  onClick={action.onClick}
                  className="rounded-button bg-app-surface px-3 py-1.5 text-xs font-semibold text-app-text shadow-sm border border-app-border hover:bg-app-surface-muted transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
                >
                  {action.label}
                </button>
              ) : (
                action
              )}
            </div>
          )}
        </div>
      </div>

      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss banner"
          className="shrink-0 rounded-button p-1 text-app-muted transition-colors hover:bg-app-surface-muted hover:text-app-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      )}
    </div>
  )
}

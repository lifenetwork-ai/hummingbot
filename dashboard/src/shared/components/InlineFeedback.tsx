import type { ReactNode } from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, Info } from 'lucide-react'
import { cn } from '../utils/cn'

export type InlineFeedbackVariant = 'error' | 'warning' | 'info' | 'success'

export type InlineFeedbackProps = {
  variant?: InlineFeedbackVariant
  message: ReactNode
  description?: ReactNode
  action?: ReactNode
  id?: string
  className?: string
}

const variantConfig: Record<InlineFeedbackVariant, {
  color: string
  Icon: typeof AlertCircle
}> = {
  error: {
    color: 'text-app-danger',
    Icon: AlertCircle,
  },
  warning: {
    color: 'text-app-warning',
    Icon: AlertTriangle,
  },
  info: {
    color: 'text-app-action',
    Icon: Info,
  },
  success: {
    color: 'text-app-success',
    Icon: CheckCircle2,
  },
}

export function InlineFeedback({
  variant = 'error',
  message,
  description,
  action,
  id,
  className,
}: InlineFeedbackProps) {
  const { color, Icon } = variantConfig[variant]
  const role = variant === 'error' ? 'alert' : 'status'

  return (
    <div
      id={id}
      role={role}
      className={cn('flex items-start gap-2 text-xs leading-normal', color, className)}
    >
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <div className="flex-1 space-y-0.5">
        <p className="font-medium">{message}</p>
        {description && (
          <p className="opacity-90">{description}</p>
        )}
        {action && (
          <div className="pt-1">{action}</div>
        )}
      </div>
    </div>
  )
}

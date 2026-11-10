import type { ReactNode } from 'react'
import { toast } from 'sonner'

export type ToastId = string | number

export type NotifyAction = {
  label: string
  onClick: (event: React.MouseEvent<HTMLButtonElement>) => void
}

export type NotifyOptions = {
  id?: ToastId
  description?: ReactNode
  action?: NotifyAction
  duration?: number
  onDismiss?: () => void
  onAutoClose?: () => void
}

export type ToastType = 'success' | 'error' | 'info' | 'warning' | 'loading'

export type NotifyUpdateOptions = NotifyOptions & {
  title?: ReactNode
  type?: ToastType
}

function resolveDuration(type: ToastType, options?: NotifyOptions): number {
  if (options?.duration !== undefined) {
    return options.duration
  }
  switch (type) {
    case 'success':
    case 'info':
      return 5000
    case 'warning':
    case 'error':
      // Warnings or errors with required action persist until closed or updated
      return options?.action ? Number.POSITIVE_INFINITY : 8000
    case 'loading':
      // Loading toasts persist until explicitly completed or dismissed
      return Number.POSITIVE_INFINITY
  }
}

function toSonnerData(type: ToastType, options?: NotifyOptions) {
  const duration = resolveDuration(type, options)
  return {
    id: options?.id,
    description: options?.description,
    action: options?.action
      ? {
          label: options.action.label,
          onClick: options.action.onClick,
        }
      : undefined,
    duration: Number.isFinite(duration) ? duration : Number.POSITIVE_INFINITY,
    onDismiss: options?.onDismiss ? () => options.onDismiss?.() : undefined,
    onAutoClose: options?.onAutoClose ? () => options.onAutoClose?.() : undefined,
  }
}

export const notify = {
  success(title: ReactNode, options?: NotifyOptions): ToastId {
    return toast.success(title, toSonnerData('success', options))
  },

  error(title: ReactNode, options?: NotifyOptions): ToastId {
    return toast.error(title, toSonnerData('error', options))
  },

  info(title: ReactNode, options?: NotifyOptions): ToastId {
    return toast.info(title, toSonnerData('info', options))
  },

  warning(title: ReactNode, options?: NotifyOptions): ToastId {
    return toast.warning(title, toSonnerData('warning', options))
  },

  loading(title: ReactNode, options?: NotifyOptions): ToastId {
    return toast.loading(title, toSonnerData('loading', options))
  },

  update(id: ToastId, options: NotifyUpdateOptions): ToastId {
    const type = options.type
    const mergedOptions: NotifyOptions = { ...options, id }

    if (type) {
      const data = toSonnerData(type, mergedOptions)
      return toast[type](options.title ?? '', data)
    }

    const data = {
      id,
      description: options.description,
      action: options.action
        ? {
            label: options.action.label,
            onClick: options.action.onClick,
          }
        : undefined,
      duration: options.duration !== undefined ? options.duration : undefined,
      onDismiss: options.onDismiss ? () => options.onDismiss?.() : undefined,
      onAutoClose: options.onAutoClose ? () => options.onAutoClose?.() : undefined,
    }
    return toast(options.title ?? '', data)
  },

  dismiss(id?: ToastId): void {
    toast.dismiss(id)
  },
}

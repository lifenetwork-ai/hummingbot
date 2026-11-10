import type { ReactNode } from 'react'
import { Banner } from '../../shared/components/Banner'

export type AuthAlertProps = {
  title: string
  description?: string
  action?: ReactNode
  className?: string
}

export function AuthAlert({ title, description, action, className }: AuthAlertProps) {
  return (
    <Banner
      variant="error"
      title={title}
      description={description}
      action={action}
      className={className}
    />
  )
}

import { Inbox } from 'lucide-react'
import type { ReactNode } from 'react'
import { Surface } from './Surface'

type EmptyStateProps = {
  title: string
  description: string
  action?: ReactNode
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <Surface className="flex flex-col items-center p-10 text-center">
      <div className="grid h-12 w-12 place-items-center rounded-card bg-app-surface-muted text-app-action">
        <Inbox size={22} />
      </div>
      <h3 className="mt-5 font-heavy text-xl font-extrabold tracking-[-0.03em]">{title}</h3>
      <p className="mt-2 max-w-md text-sm leading-6 text-app-muted">{description}</p>
      {action ? <div className="mt-6">{action}</div> : null}
    </Surface>
  )
}

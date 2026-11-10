import { AlertCircle, AlertTriangle, CheckCircle2, Info, Loader2 } from 'lucide-react'
import { Toaster } from 'sonner'
import { useTheme } from '../../theme/useTheme'

export function ToastHost() {
  const { theme } = useTheme()

  return (
    <Toaster
      position="bottom-right"
      visibleToasts={3}
      closeButton
      theme={theme}
      gap={8}
      offset={{ bottom: 24, right: 24 }}
      mobileOffset={{ bottom: 16, left: 16, right: 16 }}
      toastOptions={{
        className: 'life-toast font-sans',
        classNames: {
          toast: 'group rounded-card border border-app-border bg-app-surface text-app-text shadow-card font-sans p-4 flex items-start gap-3 w-full sm:w-[356px] text-sm',
          title: 'font-semibold text-app-text leading-snug text-sm',
          description: 'text-xs text-app-muted leading-relaxed mt-0.5',
          actionButton: 'rounded-button bg-app-action hover:bg-app-action-hover text-white text-xs font-semibold px-3 py-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-action',
          cancelButton: 'rounded-button border border-app-border bg-app-surface hover:bg-app-surface-muted text-app-muted text-xs font-medium px-3 py-1.5 transition-colors',
          closeButton: 'border border-app-border bg-app-surface hover:bg-app-surface-muted text-app-muted hover:text-app-text rounded-button transition-colors',
          success: 'border-app-success/30',
          error: 'border-app-danger/30',
          warning: 'border-app-warning/30',
          info: 'border-app-action/30',
          loading: 'border-app-border',
        },
      }}
      icons={{
        success: <CheckCircle2 className="h-4 w-4 shrink-0 text-app-success mt-0.5" />,
        error: <AlertCircle className="h-4 w-4 shrink-0 text-app-danger mt-0.5" />,
        warning: <AlertTriangle className="h-4 w-4 shrink-0 text-app-warning mt-0.5" />,
        info: <Info className="h-4 w-4 shrink-0 text-app-action mt-0.5" />,
        loading: <Loader2 className="h-4 w-4 shrink-0 text-app-action animate-spin mt-0.5" />,
      }}
    />
  )
}

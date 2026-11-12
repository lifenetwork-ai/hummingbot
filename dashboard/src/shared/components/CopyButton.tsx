import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { notify } from '../../core/feedback/notify'
import { cn } from '../utils/cn'

export interface CopyButtonProps {
  text: string
  label?: string
  className?: string
  size?: number
}

export function CopyButton({
  text,
  label = 'Copy',
  className,
  size = 12,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation()
    e.preventDefault()
    if (!text) return

    let success = false
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        success = true
      }
    } catch {
      // ignore clipboard permission/context error
    }

    if (!success) {
      try {
        const textArea = document.createElement('textarea')
        textArea.value = text
        textArea.style.position = 'fixed'
        textArea.style.left = '-999999px'
        textArea.style.top = '-999999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        success = document.execCommand('copy')
        document.body.removeChild(textArea)
      } catch {
        success = false
      }
    }

    if (success) {
      setCopied(true)
      notify.success('Copied to clipboard', { duration: 1800 })
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? 'Copied' : `${label}: ${text}`}
      aria-label={copied ? 'Copied' : `${label}: ${text}`}
      className={cn(
        'inline-flex items-center justify-center rounded p-1 transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-app-action',
        copied
          ? 'text-emerald-500 bg-emerald-500/10'
          : 'text-app-muted hover:text-app-text hover:bg-app-surface-muted',
        className
      )}
    >
      {copied ? <Check size={size} className="shrink-0" /> : <Copy size={size} className="shrink-0" />}
    </button>
  )
}

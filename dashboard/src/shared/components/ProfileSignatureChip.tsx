import { Cpu, Shuffle, SlidersHorizontal } from 'lucide-react'
import { cn } from '../utils/cn'

export interface ProfileSignatureChipProps {
  profileId: string | null | undefined
  selection?: 'fixed' | 'random' | string | null
  size?: 'sm' | 'md'
  className?: string
}

export function ProfileSignatureChip({
  profileId,
  selection,
  size = 'sm',
  className,
}: ProfileSignatureChipProps) {
  if (!profileId || profileId === '—' || profileId === 'Unconfigured' || profileId === 'Chưa cấu hình') {
    return (
      <span className={cn('inline-flex items-center gap-1 rounded-pill border border-app-border/80 bg-app-surface-muted px-2 py-0.5 font-mono text-[11px] text-app-muted', className)}>
        <span>No profile</span>
      </span>
    )
  }

  const isRandom = selection === 'random'
  const isTight = profileId.toLowerCase().includes('tight')
  const isWide = profileId.toLowerCase().includes('wide')

  return (
    <span
      data-testid="profile-signature-chip"
      title={`Strategy profile: ${profileId} (${isRandom ? 'Random rotation' : selection === 'fixed' ? 'Fixed configuration' : 'Unverified selection'})`}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-card font-mono font-bold tracking-tight shadow-xs transition-colors',
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs sm:text-sm',
        isRandom
          ? 'border border-purple-500/40 bg-purple-500/10 text-purple-400 hover:bg-purple-500/20'
          : isTight
            ? 'border border-emerald-500/40 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20'
            : isWide
              ? 'border border-sky-500/40 bg-sky-500/10 text-sky-400 hover:bg-sky-500/20'
              : 'border border-app-action/40 bg-app-action/10 text-app-action hover:bg-app-action/20',
        className
      )}
    >
      {isRandom ? (
        <Shuffle size={size === 'sm' ? 12 : 14} className="shrink-0 text-purple-400" />
      ) : isTight ? (
        <SlidersHorizontal size={size === 'sm' ? 12 : 14} className="shrink-0 text-emerald-400" />
      ) : (
        <Cpu size={size === 'sm' ? 12 : 14} className="shrink-0" />
      )}
      <span className="truncate max-w-[160px] sm:max-w-[220px]">{profileId}</span>
    </span>
  )
}

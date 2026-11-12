import { AlertTriangle, ShieldCheck, Wallet } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { ProfileSignatureChip } from '../../shared/components/ProfileSignatureChip'
import { formatLife, formatUsdt } from '../../shared/utils/formatters'
import { useAssetRibbonController } from './useAssetRibbonController'

export function AssetRibbon() {
  const model = useAssetRibbonController()
  return <aside aria-label="Persistent asset balances and positions ribbon" className="sticky top-[76px] z-30 border-b border-app-border/80 bg-app-surface/95 backdrop-blur-md shadow-xs">
    <div className="mx-auto flex w-full max-w-[1536px] flex-wrap items-center justify-between gap-2.5 px-4 py-2 text-xs sm:gap-4 sm:px-6 md:px-8 lg:px-10">
      <div className="flex min-w-0 flex-wrap items-center gap-3 sm:gap-6">
        {(model.accounts.length ? model.accounts : [{ id: null, observation: 'unknown', usdt: null, life: null }]).map((account, index) => <div key={account.id ?? index} className="flex min-w-0 flex-wrap items-center gap-2" title={`Account ${account.id ?? 'unverified'} · ${account.observation}`}>
          <Wallet size={13} className="shrink-0 text-app-action" />
          {model.accounts.length > 1 && <span className="max-w-32 truncate text-app-muted">{account.id ?? 'Unknown account'}</span>}
          <span className="text-app-muted">Available USDT:</span><span className="font-mono font-bold" title={account.usdt ?? undefined}>{formatUsdt(account.usdt)}</span>
          <span className="text-app-muted">Available LIFE:</span><span className="font-mono font-bold" title={account.life ?? undefined}>{formatLife(account.life)}</span>
          {account.observation !== 'fresh' && <span className="text-[10px] text-app-muted">({account.observation})</span>}
        </div>)}
        <div className="flex min-w-0 flex-wrap items-center gap-1.5"><span className="text-app-muted">Positions & PnL:</span><span className="font-mono font-semibold">{model.positions.length ? `${model.positions.length} recorded position(s)` : model.positionsObserved ? 'Flat within observed scope' : 'Unverified positions'}</span></div>
        {model.profileId && <div className="hidden items-center gap-1.5 lg:flex"><span className="text-app-muted">Strategy:</span><ProfileSignatureChip profileId={model.profileId} selection={model.selection} size="sm" /></div>}
      </div>
      <Badge tone={model.riskState === 'blocked' ? 'danger' : 'neutral'} className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px]">
        {model.riskState === 'blocked' ? <AlertTriangle size={10} /> : <ShieldCheck size={10} />}
        {model.riskState === 'blocked' ? 'Risk: Blocked' : model.riskState === 'no_reported_blocker' ? 'No reported blockers' : 'Capital guards: Unverified'}
      </Badge>
    </div>
  </aside>
}

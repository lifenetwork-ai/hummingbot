import { useCallback, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useOverviewController } from '../features/overview/useOverviewController'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { DataSourceErrorBanner } from '../features/overview/DataSourceErrorBanner'
import { HeldPositionsCard } from '../features/overview/HeldPositionsCard'
import { NoSessionNotice } from '../features/overview/NoSessionNotice'
import { OperatingTargetsCard } from '../features/overview/OperatingTargetsCard'
import { OperatorAttentionBanner } from '../features/overview/OperatorAttentionBanner'
import { OverviewControlBar } from '../features/overview/OverviewControlBar'
import { OverviewKpiStrip } from '../features/overview/OverviewKpiStrip'
import { RiskGuardsCard } from '../features/overview/RiskGuardsCard'
import { SessionDiagnostics } from '../features/overview/SessionDiagnostics'
import { SharedBalancesCard } from '../features/overview/SharedBalancesCard'
import { WorkingOrdersSection } from '../features/overview/WorkingOrdersSection'
import { Dialog } from '../shared/components/Dialog'
import { MotionPage } from '../shared/motion/MotionPage'

export function HomePage() {
  const [riskGuardsOpen, setRiskGuardsOpen] = useState(false)
  const closeRiskGuards = useCallback(() => setRiskGuardsOpen(false), [])
  const navigate = useNavigate()
  const context = useOperationalContext()
  const model = useOverviewController(context)
  const { status, balances, liquidity, positions } = model

  const handleBannerAction = (action: string) => {
    if (action === 'check_recovery') {
      void navigate({ to: '/admin', search: { tab: 'recovery' } })
    } else if (action === 'start') {
      void navigate({ to: '/session' })
    }
  }

  const statusData = status.data
  const balancesData = balances.data
  const positionsData = positions.data
  // When a global sync warning (reconcile/halt) is active, dim the order area
  // instead of repeating the warning box in each column.
  const globalReconcile = Boolean(statusData?.recovery?.halted)

  return (
    <MotionPage>
      <div className="mx-auto w-full max-w-[1536px] space-y-4 px-4 pb-16 pt-4 sm:space-y-5 sm:px-6 sm:pt-6 md:px-8 lg:px-10">
        <DataSourceErrorBanner
          className="space-y-4 sm:space-y-5"
          sources={[
            { name: 'Runtime status', phase: status.phase, error: status.error, data: status.data, refreshing: status.refreshing, retry: context.refreshStatus },
            { name: 'Liquidity', phase: liquidity.phase, error: liquidity.error, data: liquidity.data, refreshing: liquidity.refreshing, retry: model.refreshLiquidity },
            { name: 'Balances', phase: balances.phase, error: balances.error, data: balances.data, refreshing: balances.refreshing, retry: context.refreshBalances },
            { name: 'Positions', phase: positions.phase, error: positions.error, data: positions.data, refreshing: positions.refreshing, retry: model.refreshPositions },
          ]}
        />
        {/* Operational warnings (only when there is an incident needing intervention) */}
        <OperatorAttentionBanner
          operator={model.task.operator}
          recovery={statusData?.recovery}
          observation={model.observation.status}
          onAction={handleBannerAction}
        />
        {/* New-operator onboarding: explain NO DATA indicators before the first session */}
        <NoSessionNotice status={statusData} />

        {/* LAYER 1 — Slim control bar + 4 survival KPIs (~15% of the top area) */}
        <OverviewControlBar
          overview={model}
          observation={model.observation.status}
          isLoading={status.phase === 'loading'}
        />
        <OverviewKpiStrip
          overview={model}
          isLoading={status.phase === 'loading'}
          onOpenRiskGuards={() => setRiskGuardsOpen(true)}
        />

        {/* LAYER 2 — Operational focus: Working Orders (65%) | Exposure & Skew (35%) */}
        <div className="grid grid-cols-1 gap-4 sm:gap-5 lg:grid-cols-12 items-stretch">
          <section className="lg:col-span-8 xl:col-span-8 flex flex-col" aria-label="Liquidity and live orders on the exchange">
            <WorkingOrdersSection
              markets={model.markets}
              isLoading={liquidity.phase === 'loading'}
              dimmed={globalReconcile}
              className="h-full"
            />
          </section>
          <section className="lg:col-span-4 xl:col-span-4 flex flex-col" aria-label="Derivative positions and inventory skew">
            <HeldPositionsCard
              error={positions.error}
              positions={positionsData}
              isLoading={positions.phase === 'loading'}
              available={positions.phase === 'ready' && positionsData?.owner_available === true}
              markets={model.positionMarkets}
              className="h-full"
            />
          </section>
        </div>

        {/* LAYER 3 — Diagnostics: detailed SLA | Detailed balances & session IDs */}
        <div className="grid grid-cols-1 gap-4 sm:gap-5 lg:grid-cols-12 items-stretch">
          <section className="lg:col-span-6 xl:col-span-6 flex flex-col gap-3" aria-label="Quality commitments and 30-minute SLA">
            <OperatingTargetsCard
              markets={model.markets}
              isLoading={liquidity.phase === 'loading'}
              className="h-full"
            />
          </section>
          <section className="flex flex-col gap-3 lg:col-span-6 xl:col-span-6" aria-label="Actual asset balances and session information">
            <SharedBalancesCard
              error={balances.error}
              balances={balancesData}
              available={balances.phase === 'ready' && balancesData?.owner_available === true}
              isLoading={balances.phase === 'loading'}
              className="flex-1"
            />
            <SessionDiagnostics overview={model} observation={model.observation.status} />
          </section>
        </div>
      </div>
      <Dialog
        open={riskGuardsOpen}
        onClose={closeRiskGuards}
        title="Capital Protection"
        description="Independent status observations for each risk policy."
        placement="right"
        className="max-w-xl"
      >
        <RiskGuardsCard
          status={statusData}
          risk={model.risk}
          marketPolicies={model.risk.marketPolicies}
          positions={model.positionMarkets}
          observation={model.observation.risk}
          isLoading={status.phase === 'loading'}
        />
      </Dialog>
    </MotionPage>
  )
}

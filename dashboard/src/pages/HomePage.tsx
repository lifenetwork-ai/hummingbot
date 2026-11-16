import { useCallback, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useOverviewController } from '../features/overview/useOverviewController'
import { useOperationalContext } from '../features/operational-context/useOperationalContext'
import { CapitalRiskCard } from '../features/overview/CapitalRiskCard'
import { DataSourceErrorBanner } from '../features/overview/DataSourceErrorBanner'
import { DecisionCard } from '../features/overview/DecisionCard'
import { LiquidityVerdictCard, type DetailMode } from '../features/overview/LiquidityVerdictCard'
import { MandateBar } from '../features/overview/MandateBar'
import { NoSessionNotice } from '../features/overview/NoSessionNotice'
import { OperatorAttentionBanner } from '../features/overview/OperatorAttentionBanner'
import { OverviewKpiStripV2 } from '../features/overview/OverviewKpiStripV2'
import { ProfileExplorerDialog } from '../features/overview/ProfileExplorerDialog'
import { RiskGuardsCard } from '../features/overview/RiskGuardsCard'
import { deriveDecision } from '../features/overview/deriveDecision'
import { deriveCapitalRisk, deriveLiquidity, deriveMandate } from '../features/overview/supplement'
import { Dialog } from '../shared/components/Dialog'
import { MotionPage } from '../shared/motion/MotionPage'

export function HomePage() {
  const [riskGuardsOpen, setRiskGuardsOpen] = useState(false)
  const closeRiskGuards = useCallback(() => setRiskGuardsOpen(false), [])
  const [profileExplorerOpen, setProfileExplorerOpen] = useState(false)

  const navigate = useNavigate()
  const context = useOperationalContext()
  // Chế độ xem theo role: read = Foundation (chỉ verdict/chi phí/hành động),
  // write/admin = Operator (thấy thêm chi tiết kỹ thuật). Không có toggle thủ công.
  const mode: DetailMode = context.identity?.role === 'admin' || context.identity?.role === 'write' ? 'operator' : 'foundation'
  const model = useOverviewController(context)
  const { status, balances, liquidity, positions } = model
  const statusData = status.data

  const handleBannerAction = (action: string) => {
    if (action === 'check_recovery') {
      void navigate({ to: '/admin', search: { tab: 'recovery' } })
    } else if (action === 'start') {
      void navigate({ to: '/session' })
    }
  }

  // Supplement views — một nguồn dữ liệu duy nhất cho mọi block
  const supplement = statusData?.overview ?? null
  const mandate = deriveMandate(model, supplement)
  const liquidityMarkets = deriveLiquidity(model, supplement)
  const capital = deriveCapitalRisk(model, supplement)
  const decision = deriveDecision(liquidityMarkets, capital)

  // Anchor LIFE cho ProfileExplorer (session anchors từ /status)
  const anchors = statusData?.observed_session?.anchors ?? statusData?.session?.anchors ?? null
  const firstAnchor = anchors ? Object.values(anchors)[0] : undefined
  const anchorLifeNum = firstAnchor ? Number(firstAnchor.life) : null

  const spreadTargetForExplorer = liquidityMarkets.find((m) => m.spreadTargetBps !== null)?.spreadTargetBps ?? null

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
        <OperatorAttentionBanner
          operator={model.task.operator}
          recovery={statusData?.recovery}
          observation={model.observation.status}
          onAction={handleBannerAction}
        />
        <NoSessionNotice status={statusData} />

        {/* Q1 — nhiệm vụ được giao */}
        <MandateBar
          view={mandate}
          guards={capital.guardsSummary}
          onOpenProfileExplorer={() => setProfileExplorerOpen(true)}
        />

        {/* KPI strip — số to tường minh + target cạnh actual */}
        <OverviewKpiStripV2 liquidity={liquidityMarkets} capital={capital} />

        {/* Q3+Q4 — thanh khoản: verdict theo market + vì sao chưa đạt */}
        <section aria-label="Liquidity and live orders on the exchange">
          <LiquidityVerdictCard markets={liquidityMarkets} mode={mode} />
        </section>

        {/* Q5 — vốn & rủi ro */}
        <section aria-label="Capital, balances and risk limits">
          <CapitalRiskCard view={capital} mode={mode} riskLimits={mandate.riskLimits} onOpenRiskGuards={() => setRiskGuardsOpen(true)} />
        </section>

        {/* Q6 — đánh giá & hành động */}
        <DecisionCard decision={decision} />
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

      <ProfileExplorerDialog
        open={profileExplorerOpen}
        onClose={() => setProfileExplorerOpen(false)}
        activeProfileId={mandate.profileId}
        anchorLife={anchorLifeNum !== null && Number.isFinite(anchorLifeNum) && anchorLifeNum > 0 ? anchorLifeNum : null}
        spreadBps={spreadTargetForExplorer}
      />
    </MotionPage>
  )
}

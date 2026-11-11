import { useState } from 'react'
import { useReducedMotion } from 'framer-motion'
import { Loader2 } from 'lucide-react'
import { Badge } from '../../shared/components/Badge'
import { Surface } from '../../shared/components/Surface'
import type { OperationalContextModel } from './context'
import type { ReceiptReference } from '../../core/api/receipt-tracking'
import type { TrackedReceiptInspection } from '../../core/api/hooks'
import type { ReceiptOutcome } from '../../core/api/receipt-inspection'

function CardRefreshingOverlay({ active, label }: { active: boolean; label: string }) {
  const reducedMotion = useReducedMotion()

  return (
    <div
      className={`pointer-events-none absolute inset-0 z-10 transition-opacity duration-200 ${
        active ? 'opacity-100' : 'opacity-0'
      }`}
      aria-hidden="true"
    >
      <div className="absolute inset-x-0 top-0 z-20 h-[2px] overflow-hidden bg-app-border/20">
        <div
          className={`h-full w-1/3 rounded-pill bg-gradient-to-r from-transparent via-app-action to-transparent ${
            reducedMotion ? 'w-full opacity-60' : 'animate-shimmer'
          }`}
        />
      </div>
      <div className="absolute inset-0 bg-app-surface/30 backdrop-blur-[0.5px]" />
      <div className="relative z-10 flex items-start justify-end p-3">
        <span className="inline-flex items-center gap-1.5 rounded-pill border border-app-border/80 bg-app-surface/95 px-2.5 py-0.5 text-[11px] font-medium text-app-muted shadow-sm backdrop-blur-sm">
          <Loader2 size={11} className="animate-spin text-app-action shrink-0" />
          <span>{label}</span>
        </span>
      </div>
    </div>
  )
}

export type ContextBarProps = {
  context: OperationalContextModel
  apiBaseUrl: string
  trackedReceipts?: ReceiptReference[]
  selectedReceipt?: ReceiptReference | null
  receiptInspection?: TrackedReceiptInspection | null
  onSelectReceipt?: (receipt: ReceiptReference | null) => void
}

function environmentLabel(value: string): string {
  if (value === 'demo') return 'Demo'
  if (value === 'live') return 'Live'
  return 'Chưa xác định'
}

function observationLabel(value: 'fresh' | 'stale' | 'unknown'): string {
  if (value === 'fresh') return 'Quan sát mới nhất'
  if (value === 'stale') return 'Dữ liệu cũ'
  return 'Chưa có quan sát'
}

function ReceiptOutcomeView({ outcome, status }: { outcome: ReceiptOutcome; status?: string }) {
  if (outcome.kind === 'command') {
    return (
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-app-muted">Lệnh</dt><dd className="text-app-text">{outcome.action}</dd>
        {outcome.sessionId && <><dt className="text-app-muted">Phiên</dt><dd className="break-all font-mono text-app-text">{outcome.sessionId}</dd></>}
        {outcome.error && <><dt className="text-app-muted">Lỗi</dt><dd className="text-app-danger">{outcome.error}</dd></>}
        <dt className="col-span-2 text-app-muted">Receipt không xác nhận trạng thái lệnh hoặc vị thế hiện tại.</dt>
      </dl>
    )
  }

  if (outcome.kind === 'credential') {
    return (
      <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-app-muted">Thao tác</dt><dd className="text-app-text">{outcome.action}</dd>
        <dt className="text-app-muted">Giai đoạn</dt><dd className="text-app-text">{outcome.stage}</dd>
        {outcome.operatorActionRequired && <><dt className="text-app-muted">Cần xử lý</dt><dd className="text-app-text">Có</dd></>}
        {outcome.reasonCode && <><dt className="text-app-muted">Mã lý do</dt><dd className="font-mono text-app-text">{outcome.reasonCode}</dd></>}
        {outcome.error && <><dt className="text-app-muted">Lỗi</dt><dd className="text-app-danger">{outcome.error}</dd></>}
        <dt className="col-span-2 text-app-muted">Receipt chưa xác nhận trạng thái kết nối hiện tại.</dt>
      </dl>
    )
  }

  if (outcome.kind === 'reconciliation') {
    const stateLabel = {
      not_checked: 'Chưa kiểm tra', checking: 'Đang đối soát', matched: 'Khớp', mismatch: 'Có chênh lệch',
      incomplete: 'Chưa đầy đủ', unavailable: 'Không khả dụng',
    }[outcome.state]
    return (
      <div className="mt-2 text-xs">
        <p className="font-medium text-app-text">Kết quả đối soát: {stateLabel}</p>
        {outcome.reasons.length > 0 && <ul className="mt-1 list-disc pl-4 text-app-muted">{outcome.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      </div>
    )
  }

  const recovered = status === 'succeeded' && outcome.stage === 'recovered'
  return (
    <div className="mt-2 space-y-1 text-xs">
      <p className="text-app-text">Thao tác: {outcome.action} · Giai đoạn: {outcome.stage}</p>
      <p className="text-app-muted">{recovered ? 'Đã phục hồi theo receipt.' : outcome.canRecover ? 'Backend cho biết có thể phục hồi; cần theo đúng bước review.' : 'Backend chưa xác nhận có thể phục hồi.'}</p>
      {outcome.blockers.length > 0 && <ul className="space-y-1 text-app-muted">
        {outcome.blockers.map((blocker, index) => <li key={`${blocker.code}:${index}`}>
          <span className="font-mono">{blocker.code}</span>: {blocker.detail}{blocker.next_action && ` · ${blocker.next_action}`}
        </li>)}
      </ul>}
    </div>
  )
}

export function ContextBar({
  context,
  apiBaseUrl,
  trackedReceipts = [],
  selectedReceipt: controlledSelected,
  receiptInspection: propReceiptInspection,
  onSelectReceipt,
}: ContextBarProps) {
  const [internalSelected, setInternalSelected] = useState<ReceiptReference | null>(null)
  const isControlled = controlledSelected !== undefined
  const activeSelected = isControlled ? controlledSelected : internalSelected

  const {
    identity,
    status,
    balances,
    botId,
    processEnvironment,
    accounts,
    markets,
    environmentMismatch,
    actions,
    observation,
    observationState,
    controlVersion,
  } = context
  const roleLabel = identity?.role_label ?? 'Danh tính chưa có'
  const activePermissions = Object.entries(identity?.permissions ?? {}).filter(([, allowed]) => allowed).map(([name]) => name)

  const handleToggleReceipt = (receipt: ReceiptReference) => {
    const isSame = activeSelected?.kind === receipt.kind && activeSelected?.id === receipt.id
    const next = isSame ? null : receipt
    if (!isControlled) {
      setInternalSelected(next)
    }
    onSelectReceipt?.(next)
  }

  return (
    <section className="mx-auto w-full max-w-[1440px] px-5 py-6 md:px-10 lg:px-16" aria-labelledby="operational-context-title">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between min-h-[44px]">
        <div>
          <h2 id="operational-context-title" className="font-heavy text-2xl font-extrabold tracking-[-0.04em]">
            Ngữ cảnh vận hành
          </h2>
          <p className="mt-1 text-sm text-app-muted">
            Thông tin do API backend xác nhận; môi trường không được suy ra từ địa chỉ trang.
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-app-muted sm:self-center">
          <span>Quan sát:</span>
          {observation?.observed_at ? (
            <span className="break-all font-mono text-app-text">{observation.observed_at}</span>
          ) : (
            <span className="text-app-muted">Chưa có mốc thời gian</span>
          )}
          <Badge
            tone={
              observationState === 'fresh'
                ? 'success'
                : observationState === 'stale'
                  ? 'warning'
                  : observationState === 'session_mismatch'
                    ? 'danger'
                    : 'neutral'
            }
          >
            {observationState === 'fresh'
              ? 'Mới'
              : observationState === 'stale'
                ? 'Dữ liệu cũ'
                : observationState === 'session_mismatch'
                  ? 'Phiên không khớp'
                  : 'Chưa xác định'}
          </Badge>
          <span
            className={`inline-flex items-center gap-1.5 rounded-pill border border-app-action/30 bg-app-action/10 px-2 py-0.5 text-[11px] font-medium text-app-action transition-opacity duration-200 ${
              status.refreshing || balances.refreshing ? 'opacity-100' : 'opacity-0 pointer-events-none'
            }`}
            aria-hidden="true"
          >
            <Loader2 size={11} className="animate-spin text-app-action" />
            <span>Đang đồng bộ…</span>
          </span>
        </div>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Surface className="relative flex min-h-[340px] flex-col justify-between overflow-hidden p-5">
          <CardRefreshingOverlay
            active={Boolean(status.refreshing || balances.refreshing)}
            label={
              status.refreshing && balances.refreshing
                ? 'Đang đồng bộ bot & số dư…'
                : status.refreshing
                  ? 'Đang cập nhật bot…'
                  : 'Đang cập nhật số dư…'
            }
          />
          <div>
            <h3 className="font-semibold text-app-text">Kết nối và bot</h3>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-app-muted">API</dt>
              <dd className="break-all font-mono text-xs text-app-text">{apiBaseUrl}</dd>

              <dt className="text-app-muted">Bot</dt>
              <dd className="text-app-text">
                {botId ?? (
                  status.phase === 'error' ? (
                    'Không đọc được trạng thái'
                  ) : status.phase === 'loading' ? (
                    <span className="inline-flex items-center gap-2">
                      <span className="h-4 w-20 rounded bg-app-surface-muted animate-pulse inline-block" />
                      <span className="text-xs text-app-muted">Đang tải</span>
                    </span>
                  ) : (
                    'Đang tải'
                  )
                )}
              </dd>

              <dt className="text-app-muted">Môi trường bot</dt>
              <dd className="text-app-text">{environmentLabel(processEnvironment)}</dd>

              <dt className="text-app-muted">Tài khoản sàn</dt>
              <dd className="text-app-text">
                {accounts.length === 0 ? (
                  balances.phase === 'error' ? (
                    'Không đọc được trạng thái tài khoản'
                  ) : balances.phase === 'ready' ? (
                    'Chưa có tài khoản quan sát'
                  ) : (
                    <span className="inline-flex items-center gap-2">
                      <span className="h-4 w-28 rounded bg-app-surface-muted animate-pulse inline-block" />
                      <span className="text-xs text-app-muted">Đang tải</span>
                    </span>
                  )
                ) : accounts.length === 1 ? (
                  <div className="flex flex-wrap items-center gap-2">
                    <span>{accounts[0].data.account_id ?? 'Chưa có mã tài khoản'}</span>
                    <Badge tone={accounts[0].verification === 'verified' ? 'success' : 'neutral'}>
                      {accounts[0].verification === 'verified' ? 'Đã xác minh' : 'Chưa xác minh danh tính tài khoản'}
                    </Badge>
                    <Badge tone={accounts[0].observation === 'fresh' ? 'success' : accounts[0].observation === 'stale' ? 'warning' : 'neutral'}>
                      {observationLabel(accounts[0].observation)}
                    </Badge>
                  </div>
                ) : (
                  `Có ${accounts.length} tài khoản được backend trả về`
                )}
              </dd>

              <dt className="text-app-muted">Môi trường tài khoản</dt>
              <dd className="text-app-text">
                {accounts.length === 1
                  ? environmentLabel(accounts[0].data.environment)
                  : accounts.length > 1
                    ? 'Xem các tài khoản bên dưới'
                    : 'Chưa xác định'}
              </dd>
            </dl>

            {accounts.length > 1 && (
              <ul aria-label="Tài khoản sàn" className="mt-3 space-y-2">
                {accounts.map(({ data, verification, observation: obs }, index) => (
                  <li key={`${data.account_id ?? 'unknown'}:${index}`} className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="text-app-text">
                      {data.account_id ?? 'Chưa có mã tài khoản'} · {environmentLabel(data.environment)}
                    </span>
                    <Badge tone={verification === 'verified' ? 'success' : 'neutral'}>
                      {verification === 'verified' ? 'Đã xác minh' : 'Chưa xác minh danh tính tài khoản'}
                    </Badge>
                    <Badge tone={obs === 'fresh' ? 'success' : obs === 'stale' ? 'warning' : 'neutral'}>
                      {observationLabel(obs)}
                    </Badge>
                  </li>
                ))}
              </ul>
            )}

            {environmentMismatch && (
              <p role="alert" className="mt-3 rounded-card border border-app-danger/30 bg-app-danger/5 p-3 text-sm text-app-danger">
                Mismatch: các nguồn nhận diện môi trường từ backend không khớp. Không thể xác nhận ngữ cảnh giao dịch.
              </p>
            )}

            {status.phase === 'error' && (
              <p role="status" className="mt-3 text-sm text-app-danger">
                Không thể xác minh thông tin bot từ backend.{botId ? ' Dữ liệu được giữ lại từ lần đọc trước.' : ''}
              </p>
            )}
            {balances.phase === 'error' && (
              <p role="status" className="mt-2 text-sm text-app-danger">
                Không thể xác minh thông tin tài khoản từ backend.{accounts.length > 0 ? ' Dữ liệu được giữ lại từ lần đọc trước.' : ''}
              </p>
            )}

            <div role="status" aria-live="polite" className="sr-only">
              {status.refreshing && <p>Đang cập nhật trạng thái bot.</p>}
              {balances.refreshing && <p>Đang cập nhật số dư tài khoản.</p>}
            </div>
          </div>

          <div className="mt-4 border-t border-app-border/60 pt-3">
            <h4 className="text-sm font-semibold text-app-text">Thị trường được cấu hình</h4>
            {status.phase === 'loading' ? (
              <div className="mt-2 flex gap-2" aria-label="Đang tải thị trường">
                <span className="h-6 w-28 rounded-pill bg-app-surface-muted animate-pulse inline-block" />
                <span className="h-6 w-32 rounded-pill bg-app-surface-muted animate-pulse inline-block" />
              </div>
            ) : markets.length ? (
              <ul className="mt-2 flex flex-wrap gap-2">
                {markets.map((market) => (
                  <li key={`${market.connector}:${market.market}:${market.pair}`}>
                    <Badge tone="neutral">{market.connector} · {market.market} · {market.pair}</Badge>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-sm text-app-muted">Backend chưa cung cấp thị trường.</p>
            )}
          </div>
        </Surface>

        <Surface className="relative flex min-h-[340px] flex-col justify-between overflow-hidden p-5">
          <CardRefreshingOverlay active={Boolean(status.refreshing)} label="Đang cập nhật quyền…" />
          <div>
            <h3 className="font-semibold text-app-text">Quyền và khả năng thao tác</h3>
            <p className="mt-2 text-sm text-app-muted">
              {roleLabel} · {identity?.email ?? identity?.id ?? 'Danh tính chưa có'}
            </p>

            <div className="mt-4">
              <h4 className="text-sm font-semibold text-app-text">Khả năng hiện tại từ backend</h4>
              {status.phase === 'loading' ? (
                <ul className="mt-2 space-y-2" aria-label="Đang tải khả năng thao tác">
                  <li className="h-6 w-44 rounded-pill bg-app-surface-muted animate-pulse" />
                  <li className="h-6 w-40 rounded-pill bg-app-surface-muted animate-pulse" />
                  <li className="h-6 w-36 rounded-pill bg-app-surface-muted animate-pulse" />
                </ul>
              ) : status.phase !== 'ready' ? (
                <p className="mt-1 text-sm text-app-danger">
                  Chưa đọc được khả năng thao tác; hãy tải lại trạng thái.
                </p>
              ) : (
                <ul className="mt-2 space-y-2">
                  {Object.entries(actions).map(([name, availability]) => (
                    <li key={name} className="flex flex-wrap items-center gap-2 text-sm">
                      <Badge tone={availability.allowed ? 'success' : 'neutral'}>
                        {name}: {availability.allowed ? 'được phép' : 'bị chặn'}
                      </Badge>
                      {!availability.allowed && (
                        <span className="text-app-muted">
                          {availability.detail || availability.reasonCode || 'Backend chưa nêu lý do'}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="mt-3 text-xs text-app-muted">
              Quyền tài khoản không đảm bảo thao tác được phép: backend kiểm tra lại quyền và trạng thái khi nhận mỗi lệnh.
            </p>
          </div>

          {trackedReceipts.length > 0 && (
            <div className="mt-4 border-t border-app-border/80 pt-3">
              <h4 className="text-xs font-semibold uppercase tracking-wider text-app-muted">
                Thao tác trước đó đang theo dõi ({trackedReceipts.length})
              </h4>
              <p className="mt-1 text-xs text-app-muted">
                Chọn thao tác để kiểm tra trạng thái thực tế từ backend.
              </p>
              <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Thao tác đang theo dõi">
                {trackedReceipts.map((receipt) => {
                  const isSelected = activeSelected?.kind === receipt.kind && activeSelected?.id === receipt.id
                  return (
                    <li key={`${receipt.kind}:${receipt.id}`}>
                      <button
                        type="button"
                        onClick={() => handleToggleReceipt(receipt)}
                        title={`${receipt.kind}: ${receipt.id}`}
                        aria-pressed={isSelected}
                        className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 font-mono text-[11px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-app-action ${
                          isSelected
                            ? 'bg-app-action text-white ring-1 ring-app-action'
                            : 'bg-app-accent/10 text-app-text hover:bg-app-accent/20'
                        }`}
                      >
                        {receipt.kind}: {receipt.id.length > 12 ? `${receipt.id.slice(0, 12)}…` : receipt.id}
                      </button>
                    </li>
                  )
                })}
              </ul>

              {activeSelected && (
                <div
                  className="mt-3 rounded-card border border-app-border bg-app-surface/80 p-3 text-xs"
                  aria-label="Chi tiết thao tác đang theo dõi"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <span className="font-semibold uppercase text-app-text">{activeSelected.kind}: </span>
                      <code className="break-all font-mono text-app-text">{activeSelected.id}</code>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleToggleReceipt(activeSelected)}
                      className="shrink-0 text-xs text-app-muted hover:text-app-text"
                    >
                      Đóng
                    </button>
                  </div>

                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span className="text-app-muted">Trạng thái:</span>
                    {propReceiptInspection?.phase === 'loading' && (
                      <span className="text-app-muted">Đang đọc kết quả...</span>
                    )}
                    {propReceiptInspection?.phase === 'error' && (
                      <Badge tone="danger">
                        {propReceiptInspection.error?.message || 'Lỗi đọc trạng thái'}
                      </Badge>
                    )}
                    {propReceiptInspection?.phase === 'ready' && propReceiptInspection.status && (
                      <Badge
                        tone={
                          propReceiptInspection.status === 'failed'
                              ? 'danger'
                              : ['accepted', 'applying'].includes(propReceiptInspection.status) ? 'warning' : 'neutral'
                        }
                      >
                        Tiến trình receipt: {propReceiptInspection.status}
                      </Badge>
                    )}
                    {(!propReceiptInspection || propReceiptInspection.phase === 'idle') && (
                      <span className="text-app-muted">Đang kết nối để đọc trạng thái...</span>
                    )}
                  </div>
                  {propReceiptInspection?.phase === 'ready' && propReceiptInspection.outcome && (
                    <ReceiptOutcomeView outcome={propReceiptInspection.outcome} status={propReceiptInspection.status} />
                  )}
                </div>
              )}
            </div>
          )}
        </Surface>
      </div>

      <details className="group mt-4 rounded-card border border-app-border/80 bg-app-surface/60 p-4 transition-colors">
        <summary className="flex cursor-pointer items-center justify-between text-xs font-semibold uppercase tracking-wider text-app-muted hover:text-app-text focus:outline-none focus-visible:ring-1 focus-visible:ring-app-action">
          <span>Chi tiết kỹ thuật & chẩn đoán</span>
          <span className="text-[11px] transition-transform group-open:rotate-180">▼</span>
        </summary>
        <div className="mt-3 grid gap-3 border-t border-app-border/60 pt-3 text-xs text-app-muted sm:grid-cols-2">
          <div>
            <span className="font-medium text-app-text">API Endpoint: </span>
            <code className="break-all font-mono text-app-text">{apiBaseUrl}</code>
          </div>
          {controlVersion !== null && (
            <div>
              <span className="font-medium text-app-text">Control Version: </span>
              <code className="font-mono text-app-text">{controlVersion}</code>
            </div>
          )}
          {activePermissions.length > 0 && (
            <div className="sm:col-span-2">
              <span className="font-medium text-app-text">Quyền được cấp: </span>
              <span>{activePermissions.join(', ')}</span>
            </div>
          )}
        </div>
      </details>
    </section>
  )
}

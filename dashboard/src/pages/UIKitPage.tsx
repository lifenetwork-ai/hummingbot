import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { notify } from '../core/feedback/notify'
import { AnimatedProgress } from '../shared/components/AnimatedProgress'
import { Badge } from '../shared/components/Badge'
import { Banner } from '../shared/components/Banner'
import { Button } from '../shared/components/Button'
import { Dialog } from '../shared/components/Dialog'
import { EmptyState } from '../shared/components/EmptyState'
import { InlineFeedback } from '../shared/components/InlineFeedback'
import { Input } from '../shared/components/Input'
import { SectionLabel } from '../shared/components/SectionLabel'
import { Surface } from '../shared/components/Surface'
import { MotionPage } from '../shared/motion/MotionPage'

export function UIKitPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [progress, setProgress] = useState(64)
  const [label, setLabel] = useState('')
  const [bannerVisible, setBannerVisible] = useState(true)
  const [validationInput, setValidationInput] = useState('')

  return (
    <MotionPage>
      <div className="mx-auto max-w-[1200px] space-y-12 px-5 py-10 md:px-10 md:py-16 lg:px-16">
        <div>
          <SectionLabel tone="brand">Design system preview</SectionLabel>
          <h1 className="mt-4 font-heavy text-5xl font-black tracking-[-0.055em] md:text-6xl">UI kit.</h1>
          <p className="mt-4 max-w-2xl text-lg leading-8 text-app-muted">
            The reusable components below demonstrate the inherited LifeAI visual language.
            This page is a development preview, not a product feature.
          </p>
        </div>

        <section aria-labelledby="typography-title">
          <SectionLabel tone="brand">01 · Foundations</SectionLabel>
          <Surface className="mt-4 space-y-5 p-6 md:p-8">
            <div>
              <h2 id="typography-title" className="font-heavy text-4xl font-black tracking-[-0.055em]">Display typography</h2>
              <p className="mt-2 text-sm text-app-muted">Inter · 800/900 for headlines</p>
            </div>
            <div>
              <h3 className="text-xl font-semibold">Section heading</h3>
              <p className="mt-2 text-base leading-7 text-app-muted">IBM Plex Sans · body copy with a readable, generous line height.</p>
            </div>
            <div className="flex flex-wrap gap-3">
              {['bg-app-action', 'bg-app-accent', 'bg-app-success', 'bg-app-danger', 'bg-app-surface-muted'].map((color) => (
                <div key={color} className={`h-10 w-16 rounded-button border border-app-border ${color}`} title={color} />
              ))}
            </div>
          </Surface>
        </section>

        <section aria-labelledby="controls-title">
          <SectionLabel tone="brand">02 · Controls</SectionLabel>
          <Surface className="mt-4 space-y-7 p-6 md:p-8">
            <h2 id="controls-title" className="font-heavy text-2xl font-extrabold">Buttons & badges</h2>
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={() => setDialogOpen(true)}>Open dialog <ArrowRight size={16} /></Button>
              <Button variant="secondary">Secondary</Button>
              <Button variant="ghost">Ghost</Button>
              <Button variant="danger">Danger</Button>
              <Button disabled>Disabled</Button>
            </div>
            <div className="flex flex-wrap gap-2">
              <Badge>Neutral</Badge><Badge tone="brand">Brand</Badge><Badge tone="success">Success</Badge><Badge tone="danger">Danger</Badge>
            </div>
            <div className="max-w-md">
              <Input label="Example input" placeholder="Enter a label" value={label} onChange={(event) => setLabel(event.target.value)} hint="Local preview only — not submitted anywhere." />
            </div>
          </Surface>
        </section>

        <section aria-labelledby="states-title">
          <SectionLabel tone="brand">03 · States & motion</SectionLabel>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Surface className="space-y-5 p-6 md:p-8">
              <h2 id="states-title" className="font-heavy text-2xl font-extrabold">Animated progress</h2>
              <AnimatedProgress value={progress} ariaLabel="Example progress" />
              <div className="flex items-center justify-between text-sm">
                <span className="text-app-muted">Preview value</span>
                <span className="font-semibold tabular-nums">{progress}%</span>
              </div>
              <input aria-label="Adjust preview progress" className="w-full accent-primary" type="range" min="0" max="100" value={progress} onChange={(event) => setProgress(Number(event.target.value))} />
            </Surface>
            <EmptyState title="No items yet" description="Use this placeholder while building your first feature. Replace it with real data when the feature is ready." />
          </div>
        </section>

        <section aria-labelledby="feedback-title">
          <SectionLabel tone="brand">04 · System feedback</SectionLabel>
          <div className="mt-4 space-y-6">
            <Surface className="space-y-6 p-4 sm:p-6 md:p-8 overflow-hidden">
              <div>
                <h2 id="feedback-title" className="font-heavy text-2xl font-extrabold">Toast notifications</h2>
                <p className="mt-1 text-sm text-app-muted">
                  Interactive samples using Sonner and LifeAI styling. Click buttons to trigger. No automatic notifications.
                </p>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-app-text">Variants</h3>
                <div className="mt-2.5 flex flex-wrap gap-2.5">
                  <Button variant="secondary" onClick={() => notify.success('Configuration saved', { description: 'Trading profile parameters updated.' })}>
                    Success toast
                  </Button>
                  <Button variant="secondary" onClick={() => notify.info('Sync in progress', { description: 'Checking orderbook depth.' })}>
                    Info toast
                  </Button>
                  <Button variant="secondary" onClick={() => notify.warning('Rate limit near threshold', { description: 'Remaining requests: 12.', action: { label: 'Throttle', onClick: () => notify.dismiss() } })}>
                    Warning toast (action)
                  </Button>
                  <Button variant="danger" onClick={() => notify.error('Order submission rejected', { description: 'Exchange returned INSUFFICIENT_BALANCE.', action: { label: 'Details', onClick: () => notify.dismiss() } })}>
                    Error toast (action)
                  </Button>
                </div>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-app-text">Lifecycle & stable ID transitions</h3>
                <div className="mt-2.5 flex flex-wrap gap-2.5">
                  <Button variant="secondary" onClick={() => notify.loading('Submitting order…', { id: 'demo-lifecycle-task' })}>
                    Start loading
                  </Button>
                  <Button variant="secondary" onClick={() => notify.update('demo-lifecycle-task', { type: 'success', title: 'Order submitted', description: 'Exchange confirmed acceptance.' })}>
                    Resolve to success
                  </Button>
                  <Button variant="secondary" onClick={() => notify.update('demo-lifecycle-task', { type: 'error', title: 'Order timed out', description: 'Outcome unconfirmed. Check status before retry.' })}>
                    Resolve to error
                  </Button>
                  <Button variant="ghost" onClick={() => notify.dismiss('demo-lifecycle-task')}>
                    Dismiss loading
                  </Button>
                </div>
              </div>

              <div>
                <h3 className="text-sm font-semibold text-app-text">Identity & queue limits</h3>
                <div className="mt-2.5 flex flex-wrap gap-2.5">
                  <Button variant="secondary" onClick={() => notify.info('Single task update', { id: 'demo-stable-id', description: 'Triggering repeatedly updates this same toast.' })}>
                    Repeat same ID
                  </Button>
                  <Button variant="secondary" onClick={() => {
                    notify.info('Toast A', { id: `queue-${Date.now()}-1` })
                    notify.info('Toast B', { id: `queue-${Date.now()}-2` })
                    notify.info('Toast C', { id: `queue-${Date.now()}-3` })
                    notify.info('Toast D (exceeds limit 3)', { id: `queue-${Date.now()}-4` })
                  }}>
                    Spawn 4 toasts
                  </Button>
                  <Button variant="secondary" onClick={() => notify.error('Network timeout during batch fill calculation', {
                    id: 'demo-long-text',
                    description: 'The operation timed out after 30 seconds while aggregating multi-venue liquidity across Binance, OKX, and Bybit order books.',
                    action: { label: 'Check audit log', onClick: () => notify.dismiss('demo-long-text') },
                  })}>
                    Long content toast
                  </Button>
                </div>
              </div>
            </Surface>

            <Surface className="space-y-6 p-4 sm:p-6 md:p-8 overflow-hidden">
              <div>
                <h2 className="font-heavy text-2xl font-extrabold">Banners & inline feedback</h2>
                <p className="mt-1 text-sm text-app-muted">
                  Banners for ongoing conditions. Inline feedback for form and input validation.
                </p>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-semibold text-app-text">Persistent banner (non-dismissible)</h3>
                <Banner
                  variant="warning"
                  title="Gateway degraded performance"
                  description="WebSocket latency exceeds 800ms. Orders may experience delayed execution receipts."
                  action={{ label: 'View status', onClick: () => setDialogOpen(true) }}
                />
              </div>

              {bannerVisible ? (
                <div className="space-y-3">
                  <h3 className="text-sm font-semibold text-app-text">Dismissible banner</h3>
                  <Banner
                    variant="info"
                    title="Scheduled maintenance window"
                    description="Orderbook reconciliation will run tonight at 02:00 UTC."
                    onDismiss={() => setBannerVisible(false)}
                  />
                </div>
              ) : (
                <div>
                  <Button variant="secondary" onClick={() => setBannerVisible(true)}>
                    Restore dismissed banner
                  </Button>
                </div>
              )}

              <div className="space-y-3 pt-2">
                <h3 className="text-sm font-semibold text-app-text">Inline field validation</h3>
                <div className="max-w-md space-y-1.5">
                  <Input
                    label="Order size"
                    placeholder="0.00"
                    value={validationInput}
                    onChange={(e) => setValidationInput(e.target.value)}
                    aria-describedby="order-size-feedback"
                  />
                  {validationInput && Number.isNaN(Number(validationInput)) ? (
                    <InlineFeedback
                      id="order-size-feedback"
                      variant="error"
                      message="Invalid numeric quantity"
                      description="Please enter a valid decimal number (e.g. 1.25)."
                    />
                  ) : (
                    <InlineFeedback
                      id="order-size-feedback"
                      variant="info"
                      message="Minimum tick size is 0.001."
                    />
                  )}
                </div>
              </div>
            </Surface>
          </div>
        </section>
      </div>
      <Dialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title="Example dialog"
        description="A shared modal with keyboard dismissal and theme-aware styling."
        footer={<Button onClick={() => setDialogOpen(false)}>Close</Button>}
      >
        <p className="text-sm leading-6 text-app-muted">The dialog is a UI demonstration. No request is sent and nothing is saved.</p>
      </Dialog>
    </MotionPage>
  )
}

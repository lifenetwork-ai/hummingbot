import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { AnimatedProgress } from '../shared/components/AnimatedProgress'
import { Badge } from '../shared/components/Badge'
import { Button } from '../shared/components/Button'
import { Dialog } from '../shared/components/Dialog'
import { EmptyState } from '../shared/components/EmptyState'
import { Input } from '../shared/components/Input'
import { SectionLabel } from '../shared/components/SectionLabel'
import { Surface } from '../shared/components/Surface'
import { MotionPage } from '../shared/motion/MotionPage'

export function UIKitPage() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [progress, setProgress] = useState(64)
  const [label, setLabel] = useState('')

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

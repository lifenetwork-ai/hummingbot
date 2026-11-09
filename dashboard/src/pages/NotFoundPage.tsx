import { Link } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { buttonClassName } from '../shared/components/buttonClassName'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

export function NotFoundPage() {
  return (
    <MotionPage>
      <div className="mx-auto max-w-[1440px] px-5 py-20 md:px-10 lg:px-16">
        <section className="brand-grid relative min-h-[55vh] overflow-hidden rounded-card border border-app-border bg-app-surface p-8 md:p-14">
          <div className="pointer-events-none absolute -right-32 -top-32 h-96 w-96 rounded-full bg-app-action/10 blur-3xl" />
          <div className="relative max-w-3xl">
            <SectionLabel tone="brand">404</SectionLabel>
            <h1 className="mt-4 font-heavy text-5xl font-black leading-[0.98] tracking-[-0.055em] md:text-7xl">
              This page does not exist.
            </h1>
            <p className="mt-6 max-w-xl text-lg leading-8 text-app-muted">
              The route you opened is not registered in this starter project.
            </p>
            <Link to="/" className={buttonClassName('primary', 'mt-8')}>
              <ArrowLeft size={16} /> Back to home
            </Link>
          </div>
        </section>
      </div>
    </MotionPage>
  )
}

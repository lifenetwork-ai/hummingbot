import { Link } from '@tanstack/react-router'
import { ArrowRight, Layers3, Palette, Sparkles } from 'lucide-react'
import { buttonClassName } from '../shared/components/buttonClassName'
import { Surface } from '../shared/components/Surface'
import { SectionLabel } from '../shared/components/SectionLabel'
import { MotionPage } from '../shared/motion/MotionPage'

const foundations = [
  { icon: Layers3, title: 'Modular architecture', detail: 'Pages, features, shared primitives, core services.' },
  { icon: Palette, title: 'Design tokens', detail: 'Light/dark colors, type, radius, elevation.' },
  { icon: Sparkles, title: 'UI interactions', detail: 'Responsive layout, motion, accessible controls.' },
]

export function HomePage() {
  return (
    <MotionPage>
      <div className="mx-auto max-w-[1440px] px-5 pb-16 pt-10 md:px-10 md:pt-16 lg:px-16">
        <section className="relative overflow-hidden rounded-card border border-app-border bg-app-surface px-6 py-12 md:px-12 md:py-16 lg:px-16 lg:py-20">
          <div className="brand-grid pointer-events-none absolute inset-0 opacity-50" />
          <div className="pointer-events-none absolute -right-36 -top-36 h-[30rem] w-[30rem] rounded-full bg-app-action/10 blur-3xl" />
          <div className="pointer-events-none absolute -bottom-48 left-1/3 h-96 w-96 rounded-full bg-app-accent/10 blur-3xl" />
          <div className="relative max-w-4xl">
            <SectionLabel tone="brand">LifeAI · Frontend foundation</SectionLabel>
            <h1 className="mt-5 max-w-3xl text-balance font-heavy text-5xl font-black leading-[0.96] tracking-[-0.06em] md:text-7xl lg:text-[5.4rem]">
              A clean canvas for what comes next.
            </h1>
            <p className="mt-7 max-w-2xl text-lg leading-8 text-app-muted md:text-xl">
              A ready-to-build project with the original visual language, routing,
              theme and shared UI primitives. No product logic, wallet connection or demo data.
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link to="/ui-kit" className={buttonClassName()}>
                Explore UI kit <ArrowRight size={16} />
              </Link>
            </div>
          </div>
        </section>

        <section className="mt-14 md:mt-20" aria-labelledby="foundation-title">
          <SectionLabel tone="brand">Preserved foundation</SectionLabel>
          <h2 id="foundation-title" className="mt-3 font-heavy text-3xl font-extrabold tracking-[-0.04em] md:text-4xl">
            Only the essentials.
          </h2>
          <div className="mt-7 grid gap-4 lg:grid-cols-3">
            {foundations.map(({ icon: Icon, title, detail }) => (
              <Surface key={title} className="p-6">
                <div className="grid h-11 w-11 place-items-center rounded-card bg-app-action/10 text-app-action">
                  <Icon size={20} />
                </div>
                <h3 className="mt-6 font-heavy text-xl font-extrabold tracking-[-0.03em]">{title}</h3>
                <p className="mt-2 text-sm leading-6 text-app-muted">{detail}</p>
              </Surface>
            ))}
          </div>
        </section>
      </div>
    </MotionPage>
  )
}

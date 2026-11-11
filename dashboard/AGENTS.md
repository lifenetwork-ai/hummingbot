# Agent Instructions — LIFE MM Dashboard

This file is the **instruction contract for work in this dashboard** for coding agents (Codex, Claude Code, Cursor, and other AI assistants). Read it before modifying code. This dashboard uses the LifeAI frontend foundation for the LIFE market-making product; implement behavior against the current product requirements and backend contracts.

## 1. Mission and boundaries

- Build new frontend functionality on the existing React/TypeScript/Vite architecture.
- **Preserve the LifeAI visual system** (light/dark themes, typography, spacing, semantic colors, radii, shadows, motion and accessible interactions) unless a task explicitly asks for a redesign.
- Implement domain features and integrations only when required by the current product scope. Keep shared primitives independent of product-specific behavior.
- Do not invent backend contracts, API endpoints, user data or production credentials. Prefer typed interfaces and isolated adapters if integration details are unknown.
- Make focused changes. Do not refactor unrelated code, change tooling, or add dependencies without a concrete reason.

## 2. Mandatory reading (in order)

1. `README.md` — purpose, setup, command overview and known limitations.
2. `docs/ARCHITECTURE.md` — layering and import boundaries.
3. `docs/DESIGN_SYSTEM.md` — visual tokens and shared components.
4. `docs/UX_MOTION.md` — interaction, responsive and motion guidance.
5. `docs/TESTING.md` — tests, mocking and quality gates.
6. `docs/DEVELOPMENT.md` — local development workflow.

When implementing a feature, inspect neighboring code and tests before editing; follow established conventions rather than inventing new patterns.

## 3. Guidance ownership

Keep repository conventions in this file and implementation details in the linked
architecture, API, design and testing documents. Read the documents relevant to the
task and inspect current code before editing. Reusable agent skills should describe
methods rather than duplicate this repository's component names or business logic.

## 4. Architecture and ownership

| Location | Responsibility |
| --- | --- |
| `src/app/` | App providers, shell, global route configuration |
| `src/pages/` | Route-level composition; avoid feature business logic |
| `src/features/<feature>/` | Feature UI, hooks, types, feature-specific state and tests |
| `src/core/` | Shared service adapters and domain-neutral integrations |
| `src/shared/components/` | Accessible, reusable, business-agnostic UI primitives |
| `src/shared/motion/` | Reusable animation primitives with reduced-motion support |
| `src/shared/utils/` | Pure, reusable utilities |
| `src/theme/` | Theme configuration, generated tokens and theme provider |
| `src/test/` | Vitest setup and MSW test fixtures |
| `e2e/` | Playwright behavior, accessibility and screenshot tests |

See [Architecture](docs/ARCHITECTURE.md) and [API integration](docs/API.md) for
route protection, authenticated workspace composition and server-state ownership.

Dependency direction:
- `pages` may compose `features`, `shared`, and theme primitives.
- `features` may depend on `core`, `shared`, and `theme`, but not other unrelated features or pages.
- `shared` must not import from `features` or `pages`.
- Keep HTTP/network operations behind adapters/hooks, not inside presentational JSX.
- Use TanStack Query for server state where appropriate; do not add global state by default.

Component boundaries follow [Architecture: Component composition](docs/ARCHITECTURE.md#component-composition). Plan meaningful feature blocks before detailed page markup and review composition before delivery; do not require Atomic Design folders or wrappers for every HTML element.

## 5. UI, UX and accessibility rules

- Use semantic design tokens such as `bg-app-surface`, `text-app-text`, `text-app-action` rather than new literal hex values in application code.
- The token source of truth is `src/theme/_config.ts`; after changing tokens run `pnpm run theme:generate`. Never hand-edit `src/theme/generated/tokens.css`.
- Reuse `Button`, `Dialog`, `Input`, `Surface`, `Badge`, `EmptyState` and other existing shared components before creating new primitives.
- Support both light and dark modes; avoid styling that works only in one mode.
- Follow mobile-first/responsive layouts, appropriate focus order, visible focus states, keyboard interactions, meaningful labels, and native semantics.
- For toast, banner and inline feedback work, read [System feedback](docs/FEEDBACK.md) before implementation. It defines the planned foundation; verify source availability before calling its API.
- Respect `prefers-reduced-motion`. Keep animation purposeful and short; do not introduce perpetual animation without user value.
- Keep user-facing copy compact and clear. Never create fabricated loading, success, financial, or backend data merely to make a screen look populated.
- Note: preserving the original palette does **not** guarantee WCAG contrast compliance. Flag and verify contrast-sensitive changes rather than silently altering the brand palette.

## 6. TypeScript, React and code quality

- Use TypeScript with strict typing. Avoid `any`, unsafe casts and disabling checks to make code compile.
- Prefer small components, explicit props, pure functions and feature-local modules.
- Handle loading, empty, error and success states for async features.
- Prefer derived values over duplicated state. Avoid unnecessary effects and memoization.
- Add dependencies only when justified, and update `package.json` accordingly.
- Keep environment-specific settings out of source and `.env` secrets out of commits. `.env.example` may contain safe placeholders.
- Follow the existing ESLint configuration. Do not blanket-disable lint rules. If a narrow suppression is unavoidable, explain why next to it.

## 7. Testing requirements

**Temporary policy while dashboard UI/UX is evolving:** Do not write new E2E tests or expand existing E2E coverage when implementing dashboard features. UI/UX may change again, making repeated browser-test rewrites low-value. Defer that work until UI/UX is stable or the user explicitly requests E2E coverage. This policy overrides the E2E authoring requirements below during this phase; continue applicable unit/component tests, lint, typecheck and build checks. Preserve existing E2E tests and CI gates.

For every meaningful change:

1. Add or update Vitest tests near changed hooks, utilities and components; test behavior rather than implementation details.
2. Use React Testing Library to exercise visible output and user interactions.
3. Mock API boundaries with MSW from `src/test/mocks/`, not with hidden live network dependencies.
4. Add or update Playwright browser tests for changed navigation, forms or user-critical flows.
5. Cover keyboard accessibility; for accessibility-sensitive UI extend `e2e/accessibility.spec.ts` where useful.
6. If a visible UI/layout change affects screenshot baselines, regenerate with `pnpm run test:visual:update` **only after reviewing the visual diff**, then commit the reviewed PNGs. Never automatically approve visual changes just to turn CI green.
7. Maintain existing V8 coverage thresholds; do not lower them as a workaround.

Quality commands (run applicable ones before delivery):

```bash
pnpm run lint
pnpm run typecheck
pnpm run test:coverage
pnpm run build
pnpm run test:e2e
pnpm run test:a11y
pnpm run test:visual # once reviewed baselines are committed
```

`pnpm run check` runs lint + typecheck + coverage + production build. Browser tests require Playwright Chromium (`pnpm exec playwright install chromium`).

## 8. Standard feature workflow

For coordinated UI/logic work, follow [Coordination: Trình tự phối hợp](docs/COORDINATION.md#trình-tự-phối-hợp): analyze one workflow/TODO group, agree its scope and contract with the user, implement and test logic, hand a scoped prompt to the UI agent to build/connect components, then return to the logic owner for integrated verification. Do not expand a UI handoff to later TODO groups or close a workflow before integrated verification. Continue already-agreed local work without asking for repeated confirmation.

1. Confirm desired user-facing behavior and locate existing design patterns.
2. Define feature boundaries and types; keep business-specific code in `src/features/<feature>/`.
3. Use shared UI primitives and semantic tokens; add a new shared component only if genuinely reusable.
4. Compose the page in `src/pages/` and register it in `src/app/router.tsx`.
5. Add tests and mocks, including empty/error/keyboard states when relevant.
6. Update documentation when architecture, public commands, or design conventions change.
7. Run checks and report their **actual** results. If dependencies/tools cannot run, explicitly state which checks were not executed.

## 9. Agent completion checklist

- [ ] Read the relevant repository guidance and inspected current code.
- [ ] Feature and integration scope matches the current product requirements.
- [ ] Correct dependency boundaries and no unapproved architectural drift.
- [ ] Light/dark, mobile and keyboard behavior considered.
- [ ] Theme tokens sourced from `_config.ts` and generated as needed.
- [ ] Relevant tests added or updated; coverage gates not weakened.
- [ ] Lint, typecheck, unit/component tests and build executed (or blocker disclosed).
- [ ] Browser/visual tests executed when applicable (or blocker disclosed).
- [ ] README/docs updated if user-facing commands or conventions changed.
- [ ] Final handoff says what changed, what was verified, and what remains unverified.

## 10. Do not claim checks passed without running them

A syntax/import review is **not equivalent** to successful lint, typecheck, test, or build. If you cannot install dependencies or execute a command, report the limitation accurately and give the exact commands the developer should run locally.

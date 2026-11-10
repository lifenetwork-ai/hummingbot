# Frontend Architecture

## Source layout

```text
src/
├── app/                # app-wide providers, layout and route registration
│   ├── AppProviders.tsx # ThemeProvider and TanStack Query
│   ├── AppShell.tsx     # responsive header / footer / outlet
│   └── router.tsx       # centralized route definitions
├── core/               # shared service adapters and authentication session
├── features/           # isolated user-facing capabilities
├── pages/              # route composition only
├── shared/
│   ├── components/     # reusable branded, accessible UI primitives
│   ├── motion/         # reduced-motion-aware transitions
│   └── utils/          # generic helpers
└── theme/
    ├── _config.ts      # source of truth for token values
    ├── generated/      # generated CSS variables
    └── useTheme.tsx    # persistent light/dark theme provider
```

## Dependency rules

- `pages` compose features and shared presentation components.
- `features/<feature-name>` owns its hooks, types, API adapters and UI.
- `features` may import `core`, `shared`, and `theme`; they should not import unrelated features.
- `shared` never imports from `features` or `pages`, and remains business-agnostic.
- `core` houses external service adapters and domain types. The LIFE MM transport, typed operation adapters, session provider, React Query hooks and generated API DTOs live in `src/core/api/`; see [API](API.md).
- Prefer semantic Tailwind tokens (`bg-app-surface`, `text-app-action`) over hard-coded colors.
- Use `AppProviders` for app-wide context; don't introduce global state without a cross-cutting requirement.
- Keep API/network calls out of presentational components. Data fetching may use TanStack Query when needed.
- Respect reduced motion and keyboard accessibility.

## Component composition

Use component boundaries to express responsibilities, behavior and design-system
contracts. Native semantic HTML remains the foundation of React components; raw
markup alone is not a defect. Atomic Design is a useful vocabulary for primitives
and composed UI, not a required `atoms/molecules/organisms` folder hierarchy.

| Layer | Component responsibility |
| --- | --- |
| `pages` | Route parameters, navigation and composition of feature components; keep route-specific layout here when it has no separate responsibility. |
| `features/<name>` | Named UI blocks with product meaning, feature interactions and state. Separate orchestration from presentation when it makes either independently understandable or testable. |
| `shared/components` | Product-neutral primitives with an existing reuse need or an explicit design-system contract; no feature imports, routing policy or backend requests. |
| `app` | Application-wide shell, provider composition and route policy. |

Before building a screen, identify the existing primitives to reuse, the meaningful
feature blocks and the route's orchestration responsibilities. Extract a component
when it owns an independent interaction/state contract, repeats meaningful markup
and behavior, or centralizes an established visual/accessibility rule. A useful
feature component may have only one caller. Promote it to `shared` only when its
API and meaning are independent of that feature and reuse is justified.

Keep straightforward headings, paragraphs and layout elements inline when a wrapper
would only rename HTML or forward props without owning a contract. Avoid arbitrary
file-length limits, one component per tag, speculative generic frameworks and
boolean-heavy components that combine unrelated responsibilities.

Composition review checks:

- Does the page assemble named feature blocks, or does it own detailed feature UI
  and interactions that deserve their own boundary?
- Are repeated loading/error/empty/retry patterns using an existing primitive, or
  is a new shared contract justified by their actual common behavior?
- Can extracted presentation be rendered with explicit props without hidden
  router, network or global-state dependencies? Keep those dependencies where the
  component's responsibility requires them rather than adding pass-through layers.
- Does each extraction improve responsibility, reuse or testability while preserving
  semantics, accessible feedback, keyboard behavior and theme support?

## Adding a feature

1. Add a folder under `src/features/<name>/` with local components, hook, types and tests.
2. Add a page under `src/pages/` to compose that feature.
3. Register the route in `src/app/router.tsx` and optionally navigation in `AppShell.tsx`.
4. Reuse or extend `src/shared/components` for generic UI needs.
5. Place integration adapters in `src/core/` rather than in JSX.

## Removed from the source project

All vesting/grant flows, blockchain contracts and ABIs, chain configuration,
Wagmi/RainbowKit/viem, wallet UI, transaction state, repository mocks,
network badges, testnet/mainnet environments and product-specific documentation.
The original repository's `.git`, builds, and environment files are not included.

## Authentication boundaries

- `core/auth/` owns the shared session context/provider, identity, in-memory token
  lifecycle and backend authentication adapter. It depends on `core/api` contracts
  and never imports `features`, `pages` or `app`.
- `features/auth/` owns Google sign-in presentation and its SDK loader. It may
  consume shared authentication services from `core/auth`.
- `app/ProtectedRoute.tsx` owns route access policy; `app/redirect.ts` validates
  local return destinations. `app/AppProviders.tsx` composes auth and API providers.
- `pages/LoginPage.tsx` composes sign-in UI with the shared authentication service.

Context placement follows responsibility: feature-local context stays in its
feature; authentication services used across the app belong in `core/auth`.
Application composition and navigation policy belong in `app`.

## Environment configuration

`src/core/config/env.ts` is the single application boundary for public Vite
configuration. Consumers use its typed `getAppConfig()` interface; they do not read
`import.meta.env` or `process.env` directly. The module owns environment names and
defaults. New application environment values must be declared there and documented
in `.env.example`. ESLint enforces this boundary within `src/`.

Vite injects these values at build time; `getAppConfig()` does not fetch runtime
configuration or make deployed values mutable. Never include credentials, tokens
or other secrets in `VITE_*`. Node tooling and Playwright configuration are separate
execution environments and may read their own process environment.

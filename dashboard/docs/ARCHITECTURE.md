# Frontend Architecture

## Source layout

```text
src/
├── app/                # app-wide providers, layout and route registration
│   ├── AppProviders.tsx # ThemeProvider and TanStack Query
│   ├── AppShell.tsx     # responsive header / footer / outlet
│   └── router.tsx       # centralized route definitions
├── core/               # reserved for framework-agnostic domain adapters
├── features/           # reserved for new isolated capabilities
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
- `core` houses external service adapters and domain types. This starter intentionally ships no service adapters.
- Prefer semantic Tailwind tokens (`bg-app-surface`, `text-app-action`) over hard-coded colors.
- Use `AppProviders` for app-wide context; don't introduce global state without a cross-cutting requirement.
- Keep API/network calls out of presentational components. Data fetching may use TanStack Query when needed.
- Respect reduced motion and keyboard accessibility.

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

# Development

Requires Node.js 22 LTS and pnpm 12.4.1 (pinned in `package.json`).

```bash
pnpm install --frozen-lockfile
pnpm run dev
pnpm run theme:generate
pnpm run lint
pnpm run lint:fix
pnpm run typecheck
pnpm run test:run
pnpm run test:coverage
pnpm run build
pnpm exec playwright install chromium
pnpm run test:e2e
pnpm run test:visual:update # first time: review/commit screenshot baselines
pnpm run test:visual
```

Routes: `/login` and `/ui-kit` are public; `/` requires an approved Google login. Unknown routes use the 404 page.

Public routes render within the root `AppShell`. Authenticated routes enter
`AuthenticatedWorkspace`, which checks the session and then mounts
`OperationalProvider` → `OperationalShell` → `ContextBar` and the protected route
outlet. Shared status/balance polling starts only inside this authenticated workspace.
Keep page-specific composition in the route page; don't mount global context there.

`pnpm run dev` regenerates theme CSS before Vite starts. Configure Google client ID and reachable admin API in `.env.local` using `.env.example`; see [API](API.md). The MSW handlers are **test-only**.

CI runs lint, type checking, coverage, production build and Chromium E2E.
Visual tests start enforcing after the reviewed screenshot baselines are committed.
See [TESTING](TESTING.md) for the complete quality setup and limitations.

The UI kit does not represent a real feature; remove its route from `router.tsx`
when replacing the starter landing with a product.

Do not put secrets into `VITE_*`: these values are exposed to the browser.

[`../AGENTS.md`](../AGENTS.md) defines repository conventions for coding agents.

To preview a feature scaffold, run `pnpm run feature:scaffold account --dry-run`.
Omit `--dry-run` to create the component, test and page. Existing files are never
overwritten. Register the page under its public or authenticated route layout and
replace placeholder markup with the required behavior.

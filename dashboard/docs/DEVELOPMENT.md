# Development

Requires Node.js 22 LTS and pnpm 12.4.1 (pinned in `package.json`).

```bash
pnpm install --frozen-lockfile
pnpm run dev
pnpm run skills:check
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

Routes: `/` blank starter landing; `/ui-kit` UI preview; unknown routes use the 404 page.

`pnpm run dev` regenerates theme CSS before Vite starts. No `.env` setup, backend,
contract, credentials, or wallet is required. The MSW handlers are **test-only**.

CI runs lint, type checking, coverage, production build and Chromium E2E.
Visual tests start enforcing after the reviewed screenshot baselines are committed.
See [TESTING](TESTING.md) for the complete quality setup and limitations.

The UI kit does not represent a real feature; remove its route from `router.tsx`
when replacing the starter landing with a product.

Do not put secrets into `VITE_*`: these values are exposed to the browser.

Agent workflows and example prompts: [SKILLS.md](SKILLS.md). `AGENTS.md` remains the always-on repository instruction contract.

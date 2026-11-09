# Frontend build and deployment boundary

The repository root owns GitHub Actions workflows. A workflow nested under
`dashboard/.github/workflows/` is not discovered by GitHub for this monorepo.

## Frontend

`../../.github/workflows/frontend.yml` runs with `dashboard/` as its working
directory, Node 22 and the pnpm version pinned in `package.json`. Cache identity
uses `dashboard/pnpm-lock.yaml`. The pipeline runs lint, typecheck, coverage,
production build and browser tests. Visual comparison requires reviewed baselines.
The `dashboard-dist` artifact contains the standalone static Vite output.

From the repository root, reproduce the build with:

```bash
cd dashboard
pnpm install --frozen-lockfile
pnpm run check
pnpm exec playwright install chromium
pnpm run test:e2e
```

## Cloudflare Pages

The deploy job uses Pages Direct Upload with Wrangler 4.149.0, pinned separately
from application dependencies. It downloads `dashboard-dist` from the **same run**
and requires the entire quality job to succeed, including browser tests. It does
not rebuild or deploy the backend. Pull requests never deploy or receive the
Cloudflare token. Push/manual runs deploy only from the configured branch.
Pushes use the branch allowlist in `frontend.yml`; for a different release branch,
update that list or use manual dispatch on that branch.

All account/project/branch settings are intentionally unset. There is no default
account, project name or production branch. Until configured, the deploy job is
**SKIP**, while frontend checks/build continue and report the missing setup.
No Cloudflare resource, domain or GitHub secret is created by this change.

Configure these GitHub repository settings when the target account is selected:

| Kind | Name | Value to supply |
| --- | --- | --- |
| Variable | `CLOUDFLARE_ACCOUNT_ID` | Target account ID |
| Variable | `CLOUDFLARE_PAGES_PROJECT` | Existing Direct Upload Pages project name |
| Variable | `CLOUDFLARE_DEPLOY_BRANCH` | Release branch, matching the Pages project's production branch |
| Secret | `CLOUDFLARE_API_TOKEN` | Token scoped to the target account with Cloudflare Pages Edit permission |

Do not use the local machine's logged-in account implicitly. With the correct
account explicitly selected, provision the Direct Upload project once before CI:

```bash
# Supply values from the selected account; do not paste an API token into commands.
export CLOUDFLARE_ACCOUNT_ID='<account-id>'
export PAGES_PROJECT='<project-name>'
export PAGES_BRANCH='<production-branch>'
npx --yes wrangler@4.149.0 pages project create "$PAGES_PROJECT" --production-branch "$PAGES_BRANCH"
```

Use one deployment mechanism: GitHub Actions uploads the checked artifact; do not
also enable a second Git-connected auto-build for the same site. The job fails
if a configured deployment lacks its API token or build artifact. Upload failures
remain failures; inspect Pages deployments before retrying an uncertain upload.
A retry can create another deployment; it does not change the trading backend.
Production runs are not automatically cancelled by a newer push.

Pages provides SPA fallback when no top-level `404.html` exists. Keep the Vite
base path `/` for a root-hosted site; `/ui-kit` and other client routes must survive
a direct load/reload. No catch-all redirect or Pages Function is needed for this
static starter. The application handles unknown client routes.

After the first deploy, verify the deployment URL, direct navigation/reload,
asset loading and the configured production branch. Set up a custom domain only
for the selected account/project. Roll back through Pages to a previous successful
production deployment; backend Cloud Run revisions remain independent.

`VITE_*` values are compiled into the browser and must contain no secrets. Pages
hosting alone does not provide a gateway to an internal/IAM-protected Cloud Run
API. Backend auth/network integration is a separate requirement in
[FE integration](../../docs/fe-integration.md).

Official references:

- [Direct Upload with CI](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)
- [Pages CLI commands](https://developers.cloudflare.com/workers/wrangler/commands/pages/)
- [SPA serving behavior](https://developers.cloudflare.com/pages/configuration/serving-pages/#single-page-application-spa-rendering)

## Backend

Root backend CI and Docker workflows exclude dashboard-only changes and the
frontend workflow file. Backend image contexts exclude `/dashboard`. Backend
release events retain their explicit Docker release behavior. Mixed changes run
both relevant pipelines. Existing backend branch restrictions remain in force;
adding frontend CI for `dev` does not enable backend deployment from `dev`.

Cloud Run backend deployment remains the separate reviewed
[`make deploy-cloudrun` workflow](../../docs/cloudrun.md). GitHub Actions filters
only control GitHub Actions: any separately configured Cloud Build or hosting
trigger must have its own matching included/ignored file rules. Remote trigger
configuration is not established by these repository workflow files.

## Release checks

- Frontend-only change: frontend checks/build; backend build skipped.
- Backend-only change: backend checks/build on its configured branches; frontend skipped.
- Mixed change: both pipelines run independently.
- Frontend workflow change: frontend checks run; backend skipped if no backend files changed.
- Backend workflow/config change: backend checks remain eligible.
- Browser tests require no live exchange credentials. Hosting/auth/network and
  actual deployment acceptance remain separate from these checks.

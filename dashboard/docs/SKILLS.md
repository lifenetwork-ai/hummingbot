# Frontend Agent Skills — LifeAI FE Starter

The repository includes **10 task-specific Agent Skills** built around this project's real React 18 + TypeScript + Vite architecture, TanStack Router/Query, Tailwind theme, Vitest, MSW and Playwright setup. They complement `AGENTS.md`: the root file contains always-on rules; each skill is a **focused procedure** loaded when relevant.

## Where the skills live

**Canonical source:** `.agents/skills/<skill-name>/SKILL.md`. This path follows the Agent Skills convention and is discovered by **Codex** and **Cursor** at project level. Each file has `name` / `description` metadata, concrete steps, references and acceptance checks. Cursor can also read skills from Claude locations; avoid maintaining duplicate roots by hand.

For **Claude Code**, run the optional one-way sync:

```bash
pnpm run skills:sync:claude
```

This copies skills to `.claude/skills/`, which Claude Code discovers. `.claude/skills/` is an optional generated local copy (ignored by Git by default). Maintain only `.agents/skills/` and rerun the sync after edits. Cursor may display duplicate entries when both roots exist locally, so leave the Claude copy absent if you use only Codex/Cursor.

## Skills catalog

| Skill | Use when you ask the agent to… |
| --- | --- |
| [`feature-delivery`](../.agents/skills/feature-delivery/SKILL.md) | Implement a feature end-to-end, from page/route to tests |
| [`ui-component`](../.agents/skills/ui-component/SKILL.md) | Create/rework a reusable branded component |
| [`page-ux`](../.agents/skills/page-ux/SKILL.md) | Improve hierarchy, responsive layout or page states |
| [`design-tokens`](../.agents/skills/design-tokens/SKILL.md) | Audit/change light–dark palette, tokens, typography |
| [`routing-navigation`](../.agents/skills/routing-navigation/SKILL.md) | Add typed TanStack routes and navigation |
| [`api-server-state`](../.agents/skills/api-server-state/SKILL.md) | Connect typed APIs, TanStack Query + MSW |
| [`testing-quality`](../.agents/skills/testing-quality/SKILL.md) | Add tests, fix lint/CI, inspect coverage, visual regressions |
| [`accessibility-audit`](../.agents/skills/accessibility-audit/SKILL.md) | Audit keyboard, focus, semantic HTML, contrast |
| [`frontend-performance`](../.agents/skills/frontend-performance/SKILL.md) | Profile re-renders, slow pages, bundle/network waterfalls |
| [`bugfix-review`](../.agents/skills/bugfix-review/SKILL.md) | Debug a regression or review a PR for actionable findings |

## Example prompts

- **Codex CLI/IDE**: `Use $feature-delivery to add a profile page, keeping API integration as a typed interface until the backend contract is confirmed.`
- **Cursor Agent**: `Apply the ui-component skill to build an accessible searchable Select using the current theme. Include tests and a UI-kit example.`
- **Claude Code (after sync)**: `Use /testing-quality to add Playwright coverage for navigation and keyboard behavior.`
- **Any agent**: `Read AGENTS.md, then follow design-tokens skill to audit dark-mode contrast. Do not change the palette without discussing the impact.`

An agent may load skills based on the `description` even when you do not explicitly name them; exact invocation syntax varies by host/version. If a skill isn't discovered, restart/reload the agent workspace.

## Helper commands

```bash
pnpm run skills:check                    # Validate frontmatter + local reference links
pnpm run skills:scaffold account      # Generate a tiny component, test and page
pnpm run skills:scaffold account --dry-run
pnpm run skills:sync:claude              # Copy repository skills to Claude Code location
```

The scaffold is intentionally non-destructive, does **not** add routes, API mocks or domain data, and fails if a destination already exists. After generation, follow `routing-navigation` and replace placeholder UI with actual approved requirements.

`pnpm run check` and `.github/workflows/ci.yml` also run `skills:check`. This metadata validation is independent from npm dependencies; it does **not** mean the React build/tests have passed.

## Maintenance conventions

1. Keep the descriptions narrow enough to trigger on the right task. Prefer one clear workflow over giant all-purpose prompts.
2. Keep `SKILL.md` concise; put optional checklists and templates under a skill's `references/` directory. No arbitrary scripts, destructive cleanup or network actions as part of automatic invocation.
3. Update a skill when the repository's *actual* architecture changes, not just because an upstream trend changes.
4. Keep README + `AGENTS.md` as navigation, not copies of each skill's contents.
5. Never silently relax tests, lint, security or accessibility checks; document known gaps (including existing contrast-test exclusions and missing screenshot baseline).
6. Review skill changes like code changes. Agent skills are executable instructions to an autonomous coding tool; trust the repository before running them.

## Selected primary references

- [OpenAI — Building skills](https://learn.chatgpt.com/docs/build-skills): `SKILL.md`, progressive disclosure and project-level skills.
- [Cursor — Skills](https://cursor.com/help/customization/skills): `.agents/skills` discovery and compatibility.
- [Claude Code — Project skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview): `.claude/skills` usage.
- [React — You might not need an Effect](https://react.dev/learn/you-might-not-need-an-effect): avoid redundant state/effects.
- [TanStack Router — Code-based routing](https://tanstack.com/router/latest/docs/routing/code-based-routing): matches this starter's existing router.
- [TanStack Query — Invalidation](https://tanstack.com/query/latest/docs/framework/react/guides/query-invalidation): server-state updates.
- [Playwright — Best practices](https://playwright.dev/docs/best-practices): deterministic tests that assert user-facing behavior.
- [Vercel — React best practices](https://vercel.com/blog/introducing-react-best-practices): apply client performance guidance relevant to Vite/React, not Next.js-only patterns.

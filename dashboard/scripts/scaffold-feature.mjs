#!/usr/bin/env node
/** Optional, non-destructive starter generator for an empty UI feature. */
import { access, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const input = process.argv.slice(2)
const slug = input.find((arg) => !arg.startsWith('--'))
const allowedFlags = new Set(['--dry-run'])
if (!slug || !/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(slug) || input.some((arg) => arg.startsWith('--') && !allowedFlags.has(arg)) || input.length > 2) {
  console.error('Usage: node scripts/scaffold-feature.mjs <kebab-name> [--dry-run]')
  process.exit(1)
}

const pascal = slug.split('-').map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join('')
const title = slug.replaceAll('-', ' ')
const featureDir = path.join(repo, 'src/features', slug)
const entries = new Map([
  [path.join(featureDir, `${pascal}Section.tsx`), `import { Surface } from '../../shared/components/Surface'\n\nexport function ${pascal}Section() {\n  return (\n    <Surface className="p-6">\n      <h2 className="font-heavy text-xl font-bold capitalize">${title}</h2>\n    </Surface>\n  )\n}\n`],
  [path.join(featureDir, `${pascal}Section.test.tsx`), `import { render, screen } from '@testing-library/react'\nimport { describe, expect, it } from 'vitest'\nimport { ${pascal}Section } from './${pascal}Section'\n\ndescribe('${pascal}Section', () => {\n  it('renders its accessible heading', () => {\n    render(<${pascal}Section />)\n    expect(screen.getByRole('heading', { name: /${title}/i })).toBeInTheDocument()\n  })\n})\n`],
  [path.join(repo, 'src/pages', `${pascal}Page.tsx`), `import { ${pascal}Section } from '../features/${slug}/${pascal}Section'\nimport { MotionPage } from '../shared/motion/MotionPage'\n\nexport function ${pascal}Page() {\n  return (\n    <MotionPage>\n      <div className="mx-auto max-w-[1440px] px-5 py-10 md:px-10 lg:px-16">\n        <h1 className="mb-8 font-heavy text-3xl font-extrabold capitalize">${title}</h1>\n        <${pascal}Section />\n      </div>\n    </MotionPage>\n  )\n}\n`],
])

const collisions = []
for (const filename of entries.keys()) {
  try {
    await access(filename)
    collisions.push(path.relative(repo, filename))
  } catch {
    // No file at the destination.
  }
}
if (collisions.length) {
  console.error(`Refusing to overwrite existing files:\n- ${collisions.join('\n- ')}`)
  process.exit(1)
}

for (const filename of entries.keys()) {
  console.log(`${input.includes('--dry-run') ? 'Would create' : 'Created'}: ${path.relative(repo, filename)}`)
}
if (!input.includes('--dry-run')) {
  await mkdir(featureDir, { recursive: true })
  for (const [filename, contents] of entries) await writeFile(filename, contents, { flag: 'wx' })
  console.log('Next: implement real behavior and register the page in src/app/router.tsx.')
}

#!/usr/bin/env node
/** Validate repository Agent Skills without installing third-party dependencies. */
import { readdir, readFile, stat } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const directory = path.join(repo, '.agents', 'skills')
const problems = []
let count = 0

const names = new Set()
const entries = await readdir(directory, { withFileTypes: true })
for (const entry of entries) {
  if (!entry.isDirectory()) continue
  const file = path.join(directory, entry.name, 'SKILL.md')
  let source
  try {
    source = await readFile(file, 'utf8')
  } catch {
    problems.push(`Missing SKILL.md for ${entry.name}`)
    continue
  }
  count += 1
  const match = source.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n/)
  if (!match) {
    problems.push(`${entry.name}: missing valid YAML frontmatter`)
    continue
  }
  const frontmatter = match[1]
  const name = frontmatter.match(/^name:\s*(.+)$/m)?.[1]?.trim()
  const description = frontmatter.match(/^description:\s*(.+)$/m)?.[1]?.trim()
  if (name !== entry.name || !/^[a-z0-9-]{1,64}$/.test(name ?? '')) {
    problems.push(`${entry.name}: frontmatter name must match kebab-case directory`)
  }
  if (names.has(name)) problems.push(`${entry.name}: duplicate skill name`)
  names.add(name)
  if (!description || description.length < 50 || description.length > 1024) {
    problems.push(`${entry.name}: description must explain scope/triggers (50–1024 chars)`)
  }
  if (!source.slice(match[0].length).trim()) {
    problems.push(`${entry.name}: empty instructions`)
  }
  // Validate actual Markdown link destinations. Ignore external URLs and anchors.
  for (const link of source.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    const raw = link[1].split('#', 1)[0]
    if (!raw || /^(?:https?:|mailto:)/.test(raw)) continue
    const target = path.resolve(path.dirname(file), decodeURIComponent(raw))
    try {
      await stat(target)
    } catch {
      problems.push(`${entry.name}: broken relative link: ${link[1]}`)
    }
  }
}

if (!count) problems.push('No skills found in .agents/skills')
if (problems.length) {
  console.error(`Skill validation failed (${problems.length} error(s)):\n- ${problems.join('\n- ')}`)
  process.exitCode = 1
} else {
  console.log(`Skill validation passed: ${count} skills, valid metadata and local links.`)
}

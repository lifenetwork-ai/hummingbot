#!/usr/bin/env node
/** One-way opt-in sync: Codex/Cursor canonical skills -> Claude Code project skills. */
import { cp, mkdir, readdir } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const source = path.join(repo, '.agents/skills')
const target = path.join(repo, '.claude/skills')
const entries = (await readdir(source, { withFileTypes: true })).filter((entry) => entry.isDirectory())
await mkdir(target, { recursive: true })
for (const item of entries) {
  await cp(path.join(source, item.name), path.join(target, item.name), { recursive: true, force: true })
}
console.log(`Synced ${entries.length} project skills to .claude/skills (source: .agents/skills).`)
console.log('Edit .agents/skills only, then rerun this command; Cursor may show duplicate entries if both roots are present.')

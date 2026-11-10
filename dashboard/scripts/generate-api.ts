import { readFile, writeFile } from 'node:fs/promises'
import openapiTS, { astToString } from 'openapi-typescript'

const schema = new URL('../../docs/api/openapi.json', import.meta.url)
const target = new URL('../src/core/api/schema.d.ts', import.meta.url)
const output = '/** Generated from docs/api/openapi.json. Run pnpm run api:generate; do not edit. */\n' +
  astToString(await openapiTS(schema))
if (process.argv.includes('--check')) {
  const current = await readFile(target, 'utf8').catch(() => '')
  if (current !== output) {
    throw new Error('API types are missing or stale. Run pnpm run api:generate.')
  }
} else {
  await writeFile(target, output)
}

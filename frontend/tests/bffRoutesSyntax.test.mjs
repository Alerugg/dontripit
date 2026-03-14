import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

const routeFiles = [
  '../app/api/catalog/search/route.js',
  '../app/api/catalog/suggest/route.js',
  '../app/api/catalog/cards/[id]/route.js',
  '../app/api/catalog/prints/[id]/route.js',
]

test('catalog BFF routes keep valid error response structure', async () => {
  for (const routeFile of routeFiles) {
    const source = await fs.readFile(new URL(routeFile, import.meta.url), 'utf8')

    assert.doesNotMatch(source, /\n\s*return NextResponse\.json\(\n\s*\{ error: 'catalog_/, `${routeFile} should not contain nested return in error block`)
    assert.match(source, /\.\.\.\(developerHint \? \{ developer_hint: developerHint \} : \{\}\)/, `${routeFile} should conditionally expose developer_hint`)
    assert.match(source, /\{ status: upstream\.status \}/, `${routeFile} should preserve upstream status`)
  }
})

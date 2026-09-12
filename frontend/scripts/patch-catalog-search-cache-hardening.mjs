import fs from 'node:fs'
import path from 'node:path'

const routePath = path.resolve('app/api/catalog/search/route.js')
let source = fs.readFileSync(routePath, 'utf8')

function replaceOnce(label, from, to) {
  const count = source.split(from).length - 1
  if (count !== 1) throw new Error(`${label}: expected exactly one anchor, found ${count}`)
  source = source.replace(from, to)
}

replaceOnce(
  'metadata import',
  "import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'\n",
  "import { callInternalApi, getDeveloperErrorHint, getPublicErrorMessage } from '../../../../lib/catalog/internalApi'\nimport { CATALOG_CACHE_HEADERS, firstCleanMetadata } from '../../../../lib/catalog/metadata'\n",
)

replaceOnce(
  'legacy cache policy',
  "const PUBLIC_CACHE_HEADERS = { 'Cache-Control': 'public, s-maxage=30, stale-while-revalidate=120' }\n",
  '',
)

replaceOnce(
  'card metadata fields',
  `    set_code: matched.set_code || item.set_code || null,\n    set_name: matched.set_name || item.set_name || null,\n    collector_number: matched.collector_number || item.collector_number || null,\n    rarity: matched.rarity || item.rarity || null,\n`,
  `    set_code: firstCleanMetadata(matched.set_code, item.set_code),\n    set_name: firstCleanMetadata(matched.set_name, item.set_name),\n    collector_number: firstCleanMetadata(matched.collector_number, item.collector_number),\n    rarity: firstCleanMetadata(matched.rarity, item.rarity),\n`,
)

const cacheReferenceCount = source.split('PUBLIC_CACHE_HEADERS').length - 1
if (cacheReferenceCount !== 3) {
  throw new Error(`cache response anchors: expected 3, found ${cacheReferenceCount}`)
}
source = source.replaceAll('PUBLIC_CACHE_HEADERS', 'CATALOG_CACHE_HEADERS')

if (source.includes('stale-while-revalidate')) {
  throw new Error('stale-while-revalidate must not remain in catalog search route')
}

fs.writeFileSync(routePath, source)
console.log('catalog search route hardened: no stale serving + placeholder sanitization')

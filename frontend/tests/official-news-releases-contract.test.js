const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('live news uses official sources only', () => {
  const news = source('lib/news/service.js')
  assert.doesNotMatch(news, /pokebeach/i)
  assert.doesNotMatch(news, /mtggoldfish/i)
  assert.match(news, /pokemon\.com\/us\/pokemon-news/)
  assert.match(news, /magic\.wizards\.com\/en\/news/)
  assert.match(news, /onepiece-cardgame\.com\/topics/)
  assert.match(news, /yugioh-card\.com\/eu\/category\/news/)
  assert.match(news, /official:\s*true/)
  assert.match(news, /region/)
})

test('unknown publication dates stay unknown instead of becoming now', () => {
  const news = source('lib/news/service.js')
  assert.match(news, /published_at:\s*extractPublishedAt\(context\)/)
  assert.match(news, /published_at:\s*item\.published_at \|\| null/)
  assert.doesNotMatch(news, /published_at:\s*new Date\(\)\.toISOString\(\)/)
})

test('verified release calendar requires provenance fields', () => {
  const releases = source('lib/news/releases.js')
  const entries = [...releases.matchAll(/id:\s*'([^']+)'[\s\S]*?kind:\s*'([^']+)'/g)]
  assert.ok(entries.length >= 7, 'expected verified V1 release entries')

  for (const [block] of entries) {
    assert.match(block, /game:\s*'[^']+'/)
    assert.match(block, /title:\s*'[^']+'/)
    assert.match(block, /release_date:\s*'20\d{2}-\d{2}-\d{2}'/)
    assert.match(block, /region:\s*'(?:GLOBAL|US|EU|JP|EN)'/)
    assert.match(block, /source:\s*'[^']+'/)
    assert.match(block, /source_url:\s*'https:\/\//)
    assert.match(block, /verified_at:\s*VERIFIED_AT/)
  }
})

test('game hub consumes verified releases independently from catalogue sets', () => {
  const hub = source('components/games/GameHubPage.js')
  const client = source('lib/catalog/client.js')
  assert.match(hub, /fetchReleasesByGame/)
  assert.match(hub, /item\.source_url/)
  assert.match(hub, /REGION_LABELS/)
  assert.match(hub, /Fechas que sí están verificadas/)
  assert.doesNotMatch(hub, /return collections\s*\.filter/)
  assert.match(client, /\/api\/catalog\/releases/)
})

test('release API declares official verified provenance', () => {
  const route = source('app/api/catalog/releases/route.js')
  assert.match(route, /official_verified_calendar/)
  assert.match(route, /official-source-only/)
  assert.match(route, /getVerifiedReleases/)
})

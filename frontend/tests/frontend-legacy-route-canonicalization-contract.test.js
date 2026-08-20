const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
function source(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), 'utf8')
}

test('legacy /cards/[id] resolves identity server-side and redirects to game-scoped canonical route', () => {
  const page = source('app/cards/[id]/page.js')
  assert.doesNotMatch(page, /'use client'/)
  assert.doesNotMatch(page, /CardDetailLayout/)
  assert.match(page, /callInternalApi\(`\/api\/v1\/cards\/\$\{encodeURIComponent\(cardId\)\}`\)/)
  assert.match(page, /normalizeGameSlug\(upstream\.payload\?\.game_slug \|\| upstream\.payload\?\.game \|\| ''\)/)
  assert.match(page, /redirect\(getCardHref\(gameSlug, cardId\)\)/)
  assert.match(page, /if \(upstream\.status === 404\) notFound\(\)/)
})

test('legacy card metadata canonical points at the game-scoped card route', () => {
  const layout = source('app/cards/[id]/layout.js')
  assert.match(layout, /import \{ getCardHref \} from/)
  assert.match(layout, /const canonicalPath = gameSlug \? getCardHref\(gameSlug, id\) : '\/explorer'/)
  assert.doesNotMatch(layout, /canonical = `\$\{SITE_URL\}\/cards\//)
})

test('legacy explorer detail preserves entity type instead of treating every entity as a card', () => {
  const page = source('app/explorer/[type]/[id]/page.js')
  assert.match(page, /entityType === 'print' \|\| entityType === 'prints'/)
  assert.match(page, /entityType === 'card' \|\| entityType === 'cards'/)
  assert.match(page, /entityType === 'set' \|\| entityType === 'sets'/)
  assert.match(page, /redirect\(explorerFallback\(entityId, 'set'\)\)/)
  assert.match(page, /redirect\(explorerFallback\(entityId\)\)/)
})

test('old top-level game URLs remain compatibility redirects to /games', () => {
  const redirects = {
    pokemon: '/games/pokemon',
    magic: '/games/magic',
    onepiece: '/games/onepiece',
    yugioh: '/games/yugioh',
    riftbound: '/games/riftbound',
  }
  for (const [slug, destination] of Object.entries(redirects)) {
    const page = source(`app/${slug}/page.js`)
    assert.match(page, /redirect\(/)
    assert.ok(page.includes(destination), `${slug} must redirect to ${destination}`)
  }
})

test('sitemap publishes canonical game hubs, not top-level legacy aliases', () => {
  const sitemap = source('app/sitemap.js')
  for (const slug of ['pokemon', 'magic', 'onepiece', 'yugioh']) {
    assert.ok(sitemap.includes(`'/games/${slug}'`), `missing canonical game route ${slug}`)
    assert.ok(!sitemap.includes(`['/${slug}',`), `legacy /${slug} must not be indexed`)
  }
})

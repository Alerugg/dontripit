const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const test = require('node:test')

const ROOT = path.resolve(__dirname, '..')
const source = (file) => fs.readFileSync(path.join(ROOT, file), 'utf8')

test('game hub redirects recognized aliases to the canonical UI slug without losing query state', () => {
  const page = source('app/games/[slug]/page.js')
  assert.match(page, /const requestedSlug = String\(slug \|\| ''\)\.trim\(\)\.toLowerCase\(\)/)
  assert.match(page, /if \(requestedSlug !== game\.slug\)/)
  assert.match(page, /const queryString = searchString\(query\)/)
  assert.match(page, /redirect\(`\/games\/\$\{game\.slug\}\$\{queryString \? `\?\$\{queryString\}` : ''\}`\)/)
})

test('advanced and set-detail routes canonicalize aliases to the same game slug', () => {
  const advanced = source('app/games/[slug]/advanced/page.js')
  const setDetail = source('app/games/[slug]/sets/[setCode]/page.js')
  assert.match(advanced, /requestedSlug !== game\.slug \|\| String\(query\?\.advanced \|\| ''\) !== '1'/)
  assert.match(advanced, /redirect\(`\/games\/\$\{game\.slug\}\/advanced\?\$\{next\.toString\(\)\}`\)/)
  assert.match(advanced, /alternates: \{ canonical: `\/games\/\$\{game\.slug\}\/advanced` \}/)
  assert.match(setDetail, /if \(requestedSlug !== game\.slug\)/)
  assert.match(setDetail, /redirect\(`\/games\/\$\{game\.slug\}\/sets\/\$\{encodeURIComponent\(normalizedSetCode\.toLowerCase\(\)\)\}`\)/)
  assert.match(setDetail, /<GameSetPage gameSlug=\{game\.slug\}/)
})

test('canonical game-scoped Card route owns dynamic metadata', () => {
  const layout = source('app/games/[slug]/cards/[cardId]/layout.js')
  assert.match(layout, /generateMetadata/)
  assert.match(layout, /callInternalApi\(`\/api\/v1\/cards\/\$\{encodeURIComponent\(cardId\)\}`\)/)
  assert.match(layout, /const canonical = getCardHref\(canonicalGame\.slug, cardId\)/)
  assert.match(layout, /alternates: \{ canonical \}/)
  assert.match(layout, /openGraph:/)
  assert.match(layout, /if \(requestedSlug !== game\.slug\) redirect\(getCardHref\(game\.slug, cardId\)\)/)
})

test('legacy dynamic game namespaces resolve through the shared game catalog', () => {
  for (const file of ['app/tcg/[slug]/page.js', 'app/play/[slug]/page.js']) {
    const page = source(file)
    assert.match(page, /getGameConfig/)
    assert.match(page, /if \(!game\) notFound\(\)/)
    assert.match(page, /redirect\(`\/games\/\$\{game\.slug\}`\)/)
    assert.doesNotMatch(page, /redirect\(`\/games\/\$\{slug\}`\)/)
  }
})

test('legacy scoped Explorer keeps its search state while landing on the canonical hub', () => {
  const page = source('app/games/[slug]/explorer/page.js')
  assert.match(page, /getGameConfig/)
  assert.match(page, /const queryString = searchString\(query\)/)
  assert.match(page, /redirect\(`\/games\/\$\{game\.slug\}\$\{queryString \? `\?\$\{queryString\}` : ''\}`\)/)
})

test('Magic remains magic in public URLs and mtg only at the API boundary', () => {
  const games = source('lib/catalog/games.js')
  assert.match(games, /slug: 'magic'/)
  assert.match(games, /mtg: 'magic'/)
  assert.match(games, /magic: 'mtg'/)
})

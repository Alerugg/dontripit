const CACHE_TTL_MS = 10 * 60 * 1000
const cache = new Map()

const GENERIC_LINK_TITLES = new Set([
  'learn more',
  'read more',
  'more',
  'view more',
  'view all',
  'see more',
  'click here',
])

function decodeHtml(value = '') {
  return String(value || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/gi, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&mdash;/g, '—')
    .replace(/&ndash;/g, '–')
    .replace(/\s+/g, ' ')
    .trim()
}

function stripTags(value = '') {
  return decodeHtml(String(value || '').replace(/<[^>]+>/g, ' '))
}

function toIsoDate(value) {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : new Date(parsed).toISOString()
}

function extractStructuredPublishedAt(block = '') {
  // Only trust semantic publication markup. A random date in article copy may be
  // a release/event date and must never become the article's publication date.
  const datetime = block.match(/<time\b[^>]*datetime=["']([^"']+)["'][^>]*>/i)?.[1]
  return datetime ? toIsoDate(decodeHtml(datetime)) : null
}

function absolutize(baseUrl, href = '') {
  try {
    return new URL(href, baseUrl).toString()
  } catch {
    return href
  }
}

function uniqueByHref(items = []) {
  const seen = new Set()
  return items.filter((item) => {
    if (!item?.href || seen.has(item.href)) return false
    seen.add(item.href)
    return true
  })
}

function isUsefulTitle(title = '') {
  const normalized = String(title || '').trim().toLowerCase()
  return normalized.length >= 8
    && normalized.length <= 220
    && !GENERIC_LINK_TITLES.has(normalized)
}

async function fetchOfficialHtmlNews(url, { hrefFilter }) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/3.1',
      accept: 'text/html,application/xhtml+xml',
    },
    next: { revalidate: 600 },
  })
  if (!response.ok) throw new Error(`News fetch failed (${response.status}) for ${url}`)

  const html = await response.text()
  const anchors = [...html.matchAll(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)]

  return uniqueByHref(anchors.map((match) => {
    const href = absolutize(url, decodeHtml(match[1]))
    if (!hrefFilter(href)) return null

    const title = stripTags(match[2])
    if (!isUsefulTitle(title)) return null

    const index = match.index || 0
    const context = html.slice(Math.max(0, index - 700), Math.min(html.length, index + match[0].length + 1300))
    const paragraph = context.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)

    return {
      title,
      href,
      excerpt: stripTags(paragraph?.[1] || ''),
      published_at: extractStructuredPublishedAt(context),
    }
  }).filter(Boolean))
}

function normalizeNewsItem(item, { source, region }) {
  if (!item?.title || !item?.href) return null
  return {
    title: item.title,
    excerpt: item.excerpt || '',
    source,
    tag: 'Oficial',
    region,
    official: true,
    href: item.href,
    source_url: item.href,
    image: null,
    published_at: item.published_at || null,
  }
}

function sortByPublishedDateDesc(items) {
  return [...items].sort((a, b) => {
    const aDate = Date.parse(a.published_at || 0) || 0
    const bDate = Date.parse(b.published_at || 0) || 0
    return bDate - aDate
  })
}

async function getOnePieceNews() {
  const entries = await fetchOfficialHtmlNews('https://en.onepiece-cardgame.com/topics/', {
    hrefFilter: (href) => /en\.onepiece-cardgame\.com\/topics\/\d+\.php/i.test(href),
  })
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'ONE PIECE CARD GAME',
    region: 'EN',
  })).filter(Boolean))
}

async function getPokemonNews() {
  const entries = await fetchOfficialHtmlNews('https://www.pokemon.com/us/pokemon-news', {
    hrefFilter: (href) => /pokemon\.com\/us\/(?:news|pokemon-news)\//i.test(href),
  })
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'Pokémon.com',
    region: 'US',
  })).filter(Boolean))
}

async function getMtgNews() {
  const entries = await fetchOfficialHtmlNews('https://magic.wizards.com/en/news', {
    hrefFilter: (href) => /magic\.wizards\.com\/en\/news\/(?:announcements|feature|making-magic|mtg-arena)\//i.test(href),
  })
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'Magic: The Gathering',
    region: 'GLOBAL',
  })).filter(Boolean))
}

async function getYugiohNews() {
  const entries = await fetchOfficialHtmlNews('https://www.yugioh-card.com/eu/category/news/', {
    hrefFilter: (href) => /yugioh-card\.com\/eu\//i.test(href)
      && !/\/(?:category|product|events?|privacy|contact|about)\/?(?:$|[?#])/i.test(href),
  })
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'Yu-Gi-Oh! TCG Europe',
    region: 'EU',
  })).filter(Boolean))
}

const PROVIDERS = {
  pokemon: getPokemonNews,
  yugioh: getYugiohNews,
  mtg: getMtgNews,
  magic: getMtgNews,
  onepiece: getOnePieceNews,
}

export async function getNewsByGame(game, limit = 6) {
  const provider = PROVIDERS[game]
  if (!provider) return { items: [], provider: 'official_news', provenance: 'official-source-only' }

  const cached = cache.get(game)
  if (cached && Date.now() < cached.expiresAt) {
    return { items: cached.value.slice(0, limit), provider: 'official_news_cache', provenance: 'official-source-only' }
  }

  try {
    const items = (await provider()).slice(0, 12)
    cache.set(game, { value: items, expiresAt: Date.now() + CACHE_TTL_MS })
    return { items: items.slice(0, limit), provider: 'official_news_live', provenance: 'official-source-only' }
  } catch {
    return { items: [], provider: 'official_news_error', provenance: 'official-source-only' }
  }
}

export const __test__ = {
  extractStructuredPublishedAt,
  isUsefulTitle,
}

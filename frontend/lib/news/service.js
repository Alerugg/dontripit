const CACHE_TTL_MS = 10 * 60 * 1000
const cache = new Map()

const XML_ITEM_REGEX = /<item\b[\s\S]*?<\/item>/gi
const XML_ENTRY_REGEX = /<entry\b[\s\S]*?<\/entry>/gi
const MONTHS = 'January|February|March|April|May|June|July|August|September|October|November|December'

function decodeHtml(value = '') {
  return String(value || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/gi, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function stripTags(value = '') {
  return decodeHtml(String(value || '').replace(/<[^>]+>/g, ' '))
}

function firstTag(block, tags) {
  for (const tag of tags) {
    const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i')
    const match = block.match(regex)
    if (match?.[1]) return match[1]
  }
  return ''
}

function extractFeedLink(block) {
  const atomLink = block.match(/<link[^>]*href=["']([^"']+)["'][^>]*\/?>(?:<\/link>)?/i)
  if (atomLink?.[1]) return decodeHtml(atomLink[1])
  return decodeHtml(firstTag(block, ['link']))
}

function toIsoDate(value) {
  if (!value) return null
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? null : new Date(parsed).toISOString()
}

function extractPublishedAt(block = '') {
  const datetime = block.match(/<time\b[^>]*datetime=["']([^"']+)["'][^>]*>/i)?.[1]
  if (datetime) return toIsoDate(decodeHtml(datetime))

  const plain = stripTags(block)
  const monthFirst = plain.match(new RegExp(`\\b(${MONTHS})\\s+\\d{1,2},\\s+20\\d{2}\\b`, 'i'))?.[0]
  if (monthFirst) return toIsoDate(monthFirst)

  const dayFirst = plain.match(new RegExp(`\\b\\d{1,2}\\s+(${MONTHS})\\s+20\\d{2}\\b`, 'i'))?.[0]
  if (dayFirst) return toIsoDate(dayFirst)

  const iso = plain.match(/\b20\d{2}-\d{2}-\d{2}\b/)?.[0]
  return toIsoDate(iso)
}

function parseFeed(xml = '') {
  const blocks = xml.trim().match(XML_ITEM_REGEX) || xml.trim().match(XML_ENTRY_REGEX) || []
  return blocks.map((block) => {
    const title = stripTags(firstTag(block, ['title']))
    const excerpt = stripTags(firstTag(block, ['description', 'summary', 'content']))
    const href = extractFeedLink(block)
    const published_at = toIsoDate(decodeHtml(firstTag(block, ['pubDate', 'published', 'updated'])))
    if (!title || !href) return null
    return { title, excerpt, href, published_at }
  }).filter(Boolean)
}

async function fetchRssFeed(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/3.0',
      accept: 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
    },
    next: { revalidate: 600 },
  })
  if (!response.ok) throw new Error(`RSS fetch failed (${response.status}) for ${url}`)
  return parseFeed(await response.text())
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

async function fetchOfficialHtmlNews(url, { hrefFilter }) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/3.0',
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
    if (!title || title.length < 8 || title.length > 220) return null

    const index = match.index || 0
    const context = html.slice(Math.max(0, index - 700), Math.min(html.length, index + match[0].length + 1300))
    const paragraph = context.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)

    return {
      title,
      href,
      excerpt: stripTags(paragraph?.[1] || ''),
      // Never replace an unknown source date with the current date.
      published_at: extractPublishedAt(context),
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
  // Europe is the first official regional feed for the MVP because Don’tRipIt is
  // currently operating from Spain. Other official regions can be added without
  // mixing their dates or product calendars into this stream.
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
  extractPublishedAt,
  parseFeed,
}

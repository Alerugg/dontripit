const CACHE_TTL_MS = 10 * 60 * 1000
const cache = new Map()

const XML_ITEM_REGEX = /<item\b[\s\S]*?<\/item>/gi
const XML_ENTRY_REGEX = /<entry\b[\s\S]*?<\/entry>/gi

function decodeHtml(value = '') {
  return String(value || '')
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/gi, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
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

function parseFeed(xml = '') {
  const blocks = xml.trim().match(XML_ITEM_REGEX) || xml.trim().match(XML_ENTRY_REGEX) || []
  return blocks.map((block) => {
    const title = stripTags(firstTag(block, ['title']))
    const excerpt = stripTags(firstTag(block, ['description', 'summary', 'content']))
    const href = extractFeedLink(block)
    const published_at = decodeHtml(firstTag(block, ['pubDate', 'published', 'updated'])) || new Date().toISOString()
    if (!title || !href) return null
    return { title, excerpt, href, published_at }
  }).filter(Boolean)
}

async function fetchRssFeed(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/2.0',
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

function extractHtmlBlocks(html) {
  const containerRegex = /<(article|li|div)\b[^>]*(class=["'][^"']*(news|article|post|item)[^"']*["'])?[^>]*>[\s\S]*?<\/\1>/gi
  return html.match(containerRegex) || [html]
}

async function fetchScrapedNews(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/2.0',
      accept: 'text/html,application/xhtml+xml',
    },
    next: { revalidate: 600 },
  })
  if (!response.ok) throw new Error(`News fetch failed (${response.status}) for ${url}`)

  const blocks = extractHtmlBlocks(await response.text())
  return blocks.map((block) => {
    const linkMatch = block.match(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i)
    if (!linkMatch) return null
    const href = absolutize(url, decodeHtml(linkMatch[1]))
    const title = stripTags(linkMatch[2])
    const paragraph = block.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)
    if (!href || !title) return null
    return {
      title,
      href,
      excerpt: stripTags(paragraph?.[1] || ''),
      published_at: new Date().toISOString(),
    }
  }).filter(Boolean)
}

function normalizeNewsItem(item, { source, tag }) {
  if (!item?.title || !item?.href) return null
  return {
    title: item.title,
    excerpt: item.excerpt || '',
    source,
    tag,
    href: item.href,
    image: null,
    published_at: item.published_at || new Date().toISOString(),
  }
}

function sortByPublishedDateDesc(items) {
  return [...items].sort((a, b) => Date.parse(b.published_at || 0) - Date.parse(a.published_at || 0))
}

async function getOnePieceNews() {
  const entries = await fetchScrapedNews('https://en.onepiece-cardgame.com/news/')
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'One Piece Card Game',
    tag: 'Oficial',
  })).filter(Boolean))
}

async function getPokemonNews() {
  const feeds = [
    { url: 'https://www.pokemon.com/us/pokemon-news', source: 'Pokemon.com', tag: 'Oficial' },
    { url: 'https://pokebeach.com/feed', source: 'PokeBeach', tag: 'Comunidad' },
  ]
  const settled = await Promise.allSettled(feeds.map(async ({ url, source, tag }) => {
    const entries = await fetchRssFeed(url)
    return entries.map((item) => normalizeNewsItem(item, { source, tag })).filter(Boolean)
  }))
  return sortByPublishedDateDesc(settled.filter((result) => result.status === 'fulfilled').flatMap((result) => result.value))
}

async function getMtgNews() {
  const entries = await fetchRssFeed('https://mtggoldfish.com/feed')
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'MTGGoldfish',
    tag: 'Comunidad',
  })).filter(Boolean))
}

async function getYugiohNews() {
  const entries = await fetchRssFeed('https://www.yugioh-card.com/en/news/feed/')
  return sortByPublishedDateDesc(entries.map((item) => normalizeNewsItem(item, {
    source: 'Yu-Gi-Oh! TCG',
    tag: 'Oficial',
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
  if (!provider) return { items: [], provider: 'news_ingestion', pending_provider: false }

  const cached = cache.get(game)
  if (cached && Date.now() < cached.expiresAt) {
    return { items: cached.value.slice(0, limit), provider: 'news_ingestion_cache', pending_provider: false }
  }

  try {
    const items = (await provider()).slice(0, 12)
    cache.set(game, { value: items, expiresAt: Date.now() + CACHE_TTL_MS })
    return { items: items.slice(0, limit), provider: 'news_ingestion_live', pending_provider: false }
  } catch {
    return { items: [], provider: 'news_ingestion_error', pending_provider: false }
  }
}

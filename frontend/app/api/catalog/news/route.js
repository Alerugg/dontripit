import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'
export const revalidate = 0

const SOURCES = {
  pokemon: {
    url: 'https://play.pokemon.com/en-us/news/',
    sourceLabel: 'Play! Pokémon',
    parser: parsePokemon,
  },
  mtg: {
    url: 'https://magic.wizards.com/en/news',
    sourceLabel: 'Wizards of the Coast',
    parser: parseMagic,
  },
  onepiece: {
    url: 'https://en.onepiece-cardgame.com/topics/',
    sourceLabel: 'ONE PIECE CARD GAME',
    parser: parseOnePiece,
  },
  riftbound: {
    url: 'https://riftbound.leagueoflegends.com/en-us/news',
    sourceLabel: 'Riftbound',
    parser: parseRiftbound,
  },
  yugioh: {
    url: 'https://www.konami.com/games/eu/en/topics/',
    sourceLabel: 'KONAMI',
    parser: parseYugioh,
  },
}

function decodeHtmlEntities(value) {
  if (!value) return ''
  return String(value)
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&apos;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&#(\d+);/g, (_, code) => {
      try {
        return String.fromCharCode(Number(code))
      } catch {
        return ''
      }
    })
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => {
      try {
        return String.fromCharCode(parseInt(code, 16))
      } catch {
        return ''
      }
    })
}

function cleanText(value) {
  return decodeHtmlEntities(value || '')
    .replace(/\u00a0/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function toAbsoluteUrl(base, href) {
  try {
    return new URL(href, base).toString()
  } catch {
    return ''
  }
}

function htmlToLinkedText(html, baseUrl) {
  if (!html) return ''

  return decodeHtmlEntities(
    html
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
      .replace(
        /<a\b[^>]*href=(["'])(.*?)\1[^>]*>/gi,
        (_, __, href) => ` [[LINK:${toAbsoluteUrl(baseUrl, href)}]] `
      )
      .replace(/<\/a>/gi, ' ')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|section|article|li|ul|ol|header|footer|main|aside|h1|h2|h3|h4|h5|h6)>/gi, '\n')
      .replace(/<[^>]+>/g, ' ')
      .replace(/\r/g, '\n')
      .replace(/\n{2,}/g, '\n')
  )
}

function parseLinkLine(line) {
  const match = String(line || '').match(/\[\[LINK:([^\]]+)\]\]\s*(.*)$/)
  if (!match) return null

  return {
    href: cleanText(match[1]),
    label: cleanText(match[2]),
  }
}

function isDateLine(line) {
  const value = cleanText(line)
  return (
    /^[A-Z][a-z]+ \d{1,2}, \d{4}$/.test(value) ||
    /^\d{4}-\d{2}-\d{2}T/.test(value) ||
    /^\d{4}-\d{2}-\d{2}$/.test(value)
  )
}

function toIsoDate(value) {
  const text = cleanText(value)
  if (!text) return ''
  const parsed = new Date(text)
  if (Number.isNaN(parsed.getTime())) return text
  return parsed.toISOString()
}

function uniqueItems(items) {
  const seen = new Set()
  const out = []

  for (const item of items) {
    const href = cleanText(item.href)
    const key = href || `${cleanText(item.title)}|${cleanText(item.date)}|${cleanText(item.source)}`
    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }

  return out
}

function sortByDateDesc(items) {
  return [...items].sort((a, b) => {
    const aTime = Date.parse(a.published_at || a.date || '')
    const bTime = Date.parse(b.published_at || b.date || '')
    const aValid = Number.isFinite(aTime)
    const bValid = Number.isFinite(bTime)

    if (aValid && bValid) return bTime - aTime
    if (aValid) return -1
    if (bValid) return 1
    return 0
  })
}

function finalizeItems(items, sourceLabel) {
  return sortByDateDesc(
    uniqueItems(
      items
        .map((item) => ({
          title: cleanText(item.title),
          summary: cleanText(item.summary),
          href: cleanText(item.href),
          date: cleanText(item.date),
          published_at: cleanText(item.published_at || item.date),
          source: cleanText(item.source || sourceLabel),
          tag: cleanText(item.tag),
        }))
        .filter((item) => item.title && item.href)
    )
  )
}

function nextUsefulLine(lines, startIndex, maxLookahead = 4) {
  for (let i = startIndex; i < Math.min(lines.length, startIndex + maxLookahead); i += 1) {
    const line = cleanText(lines[i])
    if (!line) continue
    if (line.startsWith('[[LINK:')) continue
    if (isDateLine(line)) continue
    if (/^(image|arrow right|learn more|load more news)$/i.test(line)) continue
    return line
  }
  return ''
}

function parsePokemon(html, source) {
  const text = htmlToLinkedText(html, source.url)
  const lines = text.split('\n').map((line) => cleanText(line)).filter(Boolean)

  const items = []

  for (let i = 0; i < lines.length; i += 1) {
    const dateLine = lines[i]
    if (!isDateLine(dateLine)) continue

    const titleLine = cleanText(lines[i + 1])
    const maybeLink = parseLinkLine(lines[i + 2] || '')

    if (!titleLine || !maybeLink?.href) continue
    if (!/pokemon\.com|championships\.pokemon\.com/i.test(maybeLink.href)) continue
    if (!/learn more/i.test(maybeLink.label || '')) continue

    items.push({
      title: titleLine,
      summary: '',
      href: maybeLink.href,
      date: toIsoDate(dateLine),
      source: source.sourceLabel,
      tag: 'Official',
    })
  }

  return finalizeItems(items, source.sourceLabel)
}

function parseOnePiece(html, source) {
  const text = htmlToLinkedText(html, source.url)
  const lines = text.split('\n').map((line) => cleanText(line)).filter(Boolean)

  const items = []

  for (const line of lines) {
    const link = parseLinkLine(line)
    if (!link?.href || !/onepiece-cardgame\.com/i.test(link.href)) continue

    const match = link.label.match(/^(.*?)\s+([A-Z][A-Z ]+)\s+([A-Z][a-z]+ \d{1,2}, \d{4})$/)
    if (!match) continue

    const [, title, tag, date] = match
    if (!title || !date) continue

    items.push({
      title,
      summary: '',
      href: link.href,
      date: toIsoDate(date),
      source: source.sourceLabel,
      tag,
    })
  }

  return finalizeItems(items, source.sourceLabel)
}

function parseMagic(html, source) {
  const text = htmlToLinkedText(html, source.url)
  const lines = text.split('\n').map((line) => cleanText(line)).filter(Boolean)

  const items = []
  let currentTag = ''

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i]

    if (/^(Anuncios|Announcements|Feature|MTG Arena|Videos|Galería de cartas|Card Image Gallery|Preview Cards|Historia de Magic|Magic Story|Cómo creamos Magic|How We Made Magic)$/i.test(line)) {
      currentTag = line
      continue
    }

    const link = parseLinkLine(line)
    if (!link?.href) continue
    if (!/magic\.wizards\.com/i.test(link.href)) continue
    if (!/\/news\//i.test(link.href)) continue
    if (!link.label || /^(image|leer más|read more|ver las cartas|respond|more articles|más artículos)$/i.test(link.label)) continue

    const summary = nextUsefulLine(lines, i + 1, 3)

    items.push({
      title: link.label,
      summary,
      href: link.href,
      date: '',
      source: source.sourceLabel,
      tag: currentTag || 'Official',
    })
  }

  return finalizeItems(items, source.sourceLabel)
}

function parseRiftbound(html, source) {
  const compact = decodeHtmlEntities(html).replace(/\s+/g, ' ')
  const items = []
  const regex = /href="([^"]*\/en-us\/news\/[^"]+)"[^>]*>[\s\S]*?<span[^>]*>(Announcements|Organized Play|Rules and Releases|Competitive|News)[\s\S]*?(\d{4}-\d{2}-\d{2}T[^<"\s]+)[\s\S]*?<[^>]+>([^<]{6,200})<\/[^>]+>[\s\S]*?<[^>]+>([^<]{10,400})<\/[^>]+>/gi

  let match
  while ((match = regex.exec(compact)) !== null) {
    const [, href, tag, publishedAt, title, summary] = match

    items.push({
      title,
      summary,
      href: toAbsoluteUrl(source.url, href),
      date: toIsoDate(publishedAt),
      published_at: toIsoDate(publishedAt),
      source: source.sourceLabel,
      tag,
    })
  }

  return finalizeItems(items, source.sourceLabel)
}

function parseYugioh(html, source) {
  const compact = decodeHtmlEntities(html).replace(/\s+/g, ' ')
  const items = []
  const regex = /href="([^"]*\/topics\/[^"]+)"[^>]*>[\s\S]*?(YU-GI-OH![^<]{10,220})<\/[^>]+>[\s\S]*?(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})/gi

  let match
  while ((match = regex.exec(compact)) !== null) {
    const [, href, title, date] = match

    if (!/YU-GI-OH!/i.test(title)) continue

    items.push({
      title,
      summary: '',
      href: toAbsoluteUrl(source.url, href),
      date,
      source: source.sourceLabel,
      tag: 'Official',
    })
  }

  return finalizeItems(items, source.sourceLabel)
}

async function fetchHtml(url) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 15000)

  try {
    const response = await fetch(url, {
      method: 'GET',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        'user-agent': 'Mozilla/5.0 (compatible; DontripitBot/1.0; +https://dontripit.com)',
        'accept-language': 'en-US,en;q=0.9,es;q=0.8',
        accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      },
    })

    if (!response.ok) {
      throw new Error(`fetch_failed_${response.status}`)
    }

    return await response.text()
  } finally {
    clearTimeout(timeout)
  }
}

export async function GET(request) {
  const { searchParams } = new URL(request.url)
  const game = cleanText(searchParams.get('game')).toLowerCase()
  const limit = Math.max(1, Math.min(Number(searchParams.get('limit') || 6), 12))

  if (!game || !SOURCES[game]) {
    return NextResponse.json(
      {
        error: 'invalid_game',
        message: 'game inválido para noticias.',
      },
      { status: 400 },
    )
  }

  const source = SOURCES[game]

  try {
    const html = await fetchHtml(source.url)
    const parsedItems = source.parser(html, source)
    return NextResponse.json({
      items: parsedItems.slice(0, limit),
    })
  } catch (error) {
    return NextResponse.json({
      items: [],
      warning: 'catalog_news_empty',
      developer_hint: error instanceof Error ? error.message : 'unknown_news_error',
    })
  }
}
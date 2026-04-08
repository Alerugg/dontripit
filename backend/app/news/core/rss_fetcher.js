const XML_ITEM_REGEX = /<item\b[\s\S]*?<\/item>/gi
const XML_ENTRY_REGEX = /<entry\b[\s\S]*?<\/entry>/gi

function decodeHtml(value = '') {
  return value
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
  return decodeHtml(value.replace(/<[^>]+>/g, ' '))
}

function firstTag(block, tags) {
  for (const tag of tags) {
    const regex = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i')
    const match = block.match(regex)
    if (match?.[1]) {
      return match[1]
    }
  }
  return ''
}

function extractLink(block) {
  const atomLink = block.match(/<link[^>]*href=["']([^"']+)["'][^>]*\/?>(?:<\/link>)?/i)
  if (atomLink?.[1]) {
    return decodeHtml(atomLink[1])
  }

  const plain = firstTag(block, ['link'])
  return decodeHtml(plain)
}

function extractDate(block) {
  return decodeHtml(firstTag(block, ['pubDate', 'published', 'updated'])) || new Date().toISOString()
}

function parseEntries(xml = '') {
  const trimmed = xml.trim()
  const blocks = trimmed.match(XML_ITEM_REGEX) || trimmed.match(XML_ENTRY_REGEX) || []

  return blocks
    .map((block) => {
      const title = stripTags(firstTag(block, ['title']))
      const excerpt = stripTags(firstTag(block, ['description', 'summary', 'content']))
      const href = extractLink(block)
      const published_at = extractDate(block)

      if (!title || !href) {
        return null
      }

      return {
        title,
        excerpt,
        href,
        published_at,
      }
    })
    .filter(Boolean)
}

export async function fetchRssFeed(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/1.0',
      accept: 'application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8',
    },
    next: { revalidate: 600 },
  })

  if (!response.ok) {
    throw new Error(`RSS fetch failed (${response.status}) for ${url}`)
  }

  const xml = await response.text()
  return parseEntries(xml)
}

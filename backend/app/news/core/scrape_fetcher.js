function decodeHtml(value = '') {
  return value
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

function absolutize(baseUrl, href = '') {
  try {
    return new URL(href, baseUrl).toString()
  } catch {
    return href
  }
}

function extractBlocks(html) {
  const containerRegex = /<(article|li|div)\b[^>]*(class=["'][^"']*(news|article|post|item)[^"']*["'])?[^>]*>[\s\S]*?<\/\1>/gi
  const blocks = html.match(containerRegex)
  return blocks?.length ? blocks : [html]
}

function extractLink(block) {
  const match = block.match(/<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/i)
  if (!match) {
    return null
  }

  return {
    href: decodeHtml(match[1]),
    title: stripTags(match[2]),
  }
}

function extractExcerpt(block) {
  const p = block.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)
  if (p?.[1]) {
    return stripTags(p[1])
  }

  const div = block.match(/<div\b[^>]*(class=["'][^"']*(excerpt|summary|text)[^"']*["'])[^>]*>([\s\S]*?)<\/div>/i)
  return stripTags(div?.[3] || '')
}

export async function fetchScrapedNews(url) {
  const response = await fetch(url, {
    headers: {
      'user-agent': 'dontripit-news-bot/1.0',
      accept: 'text/html,application/xhtml+xml',
    },
    next: { revalidate: 600 },
  })

  if (!response.ok) {
    throw new Error(`Scrape fetch failed (${response.status}) for ${url}`)
  }

  const html = await response.text()
  const blocks = extractBlocks(html)

  return blocks
    .map((block) => {
      const link = extractLink(block)
      if (!link?.href || !link.title) {
        return null
      }

      return {
        title: link.title,
        href: absolutize(url, link.href),
        excerpt: extractExcerpt(block),
        published_at: new Date().toISOString(),
      }
    })
    .filter(Boolean)
}

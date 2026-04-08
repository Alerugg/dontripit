export function normalizeNewsItem(item, { source, tag }) {
  if (!item?.title || !item?.href) {
    return null
  }

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

export function sortByPublishedDateDesc(items) {
  return [...items].sort((a, b) => {
    const aTs = Date.parse(a.published_at || 0)
    const bTs = Date.parse(b.published_at || 0)
    return bTs - aTs
  })
}

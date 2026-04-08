import { fetchRssFeed } from '../core/rss_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const FEED_URL = 'https://www.yugioh-card.com/en/news/feed/'

export async function getYugiohNews() {
  const entries = await fetchRssFeed(FEED_URL)
  return sortByPublishedDateDesc(
    entries
      .map((item) => normalizeNewsItem(item, { source: 'Yu-Gi-Oh! TCG', tag: 'Oficial' }))
      .filter(Boolean),
  )
}

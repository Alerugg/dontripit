import { fetchRssFeed } from '../core/rss_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const FEED_URL = 'https://mtggoldfish.com/feed'

export async function getMtgNews() {
  const entries = await fetchRssFeed(FEED_URL)
  return sortByPublishedDateDesc(
    entries
      .map((item) => normalizeNewsItem(item, { source: 'MTGGoldfish', tag: 'Comunidad' }))
      .filter(Boolean),
  )
}

import { fetchScrapedNews } from '../core/scrape_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const NEWS_URL = 'https://magic.wizards.com/en/news'

export async function getMtgNews() {
  const entries = await fetchScrapedNews(NEWS_URL)
  return sortByPublishedDateDesc(
    entries
      .map((item) => normalizeNewsItem(item, { source: 'Magic: The Gathering / Wizards', tag: 'Oficial' }))
      .filter(Boolean),
  )
}

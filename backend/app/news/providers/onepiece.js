import { fetchScrapedNews } from '../core/scrape_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const NEWS_URL = 'https://en.onepiece-cardgame.com/news/'

export async function getOnePieceNews() {
  const entries = await fetchScrapedNews(NEWS_URL)
  return sortByPublishedDateDesc(
    entries
      .map((item) => normalizeNewsItem(item, { source: 'One Piece Card Game', tag: 'Oficial' }))
      .filter(Boolean),
  )
}

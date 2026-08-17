import { fetchScrapedNews } from '../core/scrape_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const NEWS_URL = 'https://www.pokemon.com/us/pokemon-news'

export async function getPokemonNews() {
  const entries = await fetchScrapedNews(NEWS_URL)
  return sortByPublishedDateDesc(
    entries
      .map((item) => normalizeNewsItem(item, { source: 'Pokemon.com', tag: 'Oficial' }))
      .filter(Boolean),
  )
}

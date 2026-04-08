import { fetchRssFeed } from '../core/rss_fetcher.js'
import { normalizeNewsItem, sortByPublishedDateDesc } from './utils.js'

const FEEDS = [
  {
    url: 'https://www.pokemon.com/us/pokemon-news',
    source: 'Pokemon.com',
    tag: 'Oficial',
  },
  {
    url: 'https://pokebeach.com/feed',
    source: 'PokeBeach',
    tag: 'Comunidad',
  },
]

export async function getPokemonNews() {
  const settled = await Promise.allSettled(
    FEEDS.map(async ({ url, source, tag }) => {
      const entries = await fetchRssFeed(url)
      return entries.map((item) => normalizeNewsItem(item, { source, tag })).filter(Boolean)
    }),
  )

  const merged = settled
    .filter((result) => result.status === 'fulfilled')
    .flatMap((result) => result.value)

  return sortByPublishedDateDesc(merged)
}

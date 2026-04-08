import { getCachedNews, setCachedNews } from './core/cache.js'
import { getPokemonNews } from './providers/pokemon.js'
import { getYugiohNews } from './providers/yugioh.js'
import { getMtgNews } from './providers/mtg.js'
import { getOnePieceNews } from './providers/onepiece.js'

const PROVIDERS = {
  pokemon: { key: 'pokemon', fn: getPokemonNews },
  yugioh: { key: 'yugioh', fn: getYugiohNews },
  mtg: { key: 'mtg', fn: getMtgNews },
  magic: { key: 'mtg', fn: getMtgNews },
  onepiece: { key: 'onepiece', fn: getOnePieceNews },
}

export async function getNewsByGame(game, limit = 6) {
  const selected = PROVIDERS[game]
  if (!selected) {
    return {
      items: [],
      provider: 'news_ingestion',
      pending_provider: false,
    }
  }

  const cached = getCachedNews(selected.key)
  if (cached) {
    return {
      items: cached.slice(0, limit),
      provider: 'news_ingestion_cache',
      pending_provider: false,
    }
  }

  try {
    const items = (await selected.fn()).slice(0, 6)
    setCachedNews(selected.key, items)

    return {
      items: items.slice(0, limit),
      provider: 'news_ingestion_live',
      pending_provider: false,
    }
  } catch {
    return {
      items: [],
      provider: 'news_ingestion_error',
      pending_provider: false,
    }
  }
}

'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import SearchInput from '../search/SearchInput'
import { suggestCatalog } from '../../lib/catalog/client'
import { ACTIVE_GAME_CATALOG } from '../../lib/catalog/games'
import { getCardHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'
import './HomeSearchV2.css'

function suggestionHref(item) {
  if (item?.type === 'print' && item.id) return getPrintHref(item.id)
  if (item?.type === 'set' && item.game && (item.set_code || item.code)) {
    return getSetHref(item.game, item.set_code || item.code)
  }
  if (item?.type === 'card' && item.game && (item.card_id || item.id)) {
    return getCardHref(item.game, item.card_id || item.id)
  }
  return ''
}

function scopedSearchHref(game, query) {
  const suffix = query ? `?q=${encodeURIComponent(query)}&kind=card&view=grid` : ''
  return game ? `/games/${game}${suffix}` : `/explorer${suffix}`
}

export default function HomeSearch() {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [selectedGame, setSelectedGame] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)

  const selectedConfig = useMemo(
    () => ACTIVE_GAME_CATALOG.find((game) => game.slug === selectedGame) || null,
    [selectedGame],
  )

  useEffect(() => {
    const clean = query.trim()
    if (!clean) {
      setSuggestions([])
      setLoading(false)
      return undefined
    }

    let cancelled = false
    const controller = new AbortController()
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const rows = await suggestCatalog(
          { q: clean, game: selectedGame, limit: 8 },
          { signal: controller.signal },
        )
        if (!cancelled) setSuggestions(rows || [])
      } catch (requestError) {
        if (!cancelled && requestError?.name !== 'AbortError') setSuggestions([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, 180)

    return () => {
      cancelled = true
      controller.abort()
      clearTimeout(timer)
    }
  }, [query, selectedGame])

  function chooseGame(game) {
    setSelectedGame(game)
    setSuggestions([])
  }

  function submit() {
    const clean = query.trim()
    router.push(scopedSearchHref(selectedGame, clean))
  }

  function selectSuggestion(item) {
    const href = suggestionHref(item)
    if (href) {
      router.push(href)
      return
    }
    const clean = String(item?.title || item?.name || query || '').trim()
    if (clean) router.push(scopedSearchHref(selectedGame, clean))
  }

  return (
    <div className="v17-home-search-wrap">
      <div className="v17-home-search-shell">
        <div className="v17-home-search-games" role="group" aria-label="Elegir juego para la búsqueda">
          <button
            type="button"
            className={`v17-game-scope ${selectedGame === '' ? 'is-active' : ''}`}
            aria-pressed={selectedGame === ''}
            aria-label="Buscar en todos los juegos"
            data-game="all"
            onClick={() => chooseGame('')}
          >
            Todos
          </button>
          {ACTIVE_GAME_CATALOG.map((game) => (
            <button
              key={game.slug}
              type="button"
              className={`v17-game-scope ${selectedGame === game.slug ? 'is-active' : ''}`}
              aria-pressed={selectedGame === game.slug}
              aria-label={`Buscar solo en ${game.name}`}
              data-game={game.slug}
              onClick={() => chooseGame(game.slug)}
              style={{ '--scope-accent': game.accent }}
            >
              <i aria-hidden="true" />
              {game.name === 'Magic: The Gathering' ? 'Magic' : game.name === 'ONE PIECE Card Game' ? 'One Piece' : game.name}
            </button>
          ))}
        </div>

        <div className="v17-home-search-input">
          <SearchInput
            type="search"
            value={query}
            onChange={setQuery}
            onSubmit={submit}
            suggestions={suggestions}
            suggestionsLoading={loading}
            onSuggestionSelect={selectSuggestion}
            placeholder="Pikachu, Luffy, Black Lotus, Dark Magician…"
            variant="hero"
          />
        </div>
      </div>

      <div className="v17-home-search-meta" aria-label="Alcance de búsqueda">
        <small>
          Buscando en <strong>{selectedConfig?.name || 'todos los TCG'}</strong>
        </small>
        <button
          type="button"
          className="v17-home-hub-link"
          onClick={() => router.push(selectedGame ? `/games/${selectedGame}` : '/explorer')}
        >
          {selectedConfig ? `Abrir hub de ${selectedConfig.name} →` : 'Abrir explorador →'}
        </button>
      </div>
    </div>
  )
}

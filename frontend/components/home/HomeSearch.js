'use client'

import { useEffect, useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import SearchInput from '../search/SearchInput'
import { suggestCatalog } from '../../lib/catalog/client'
import { ACTIVE_GAME_CATALOG } from '../../lib/catalog/games'
import { getCardHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'
import './HomeSearchV2.css'

const QUICK_SEARCHES = [
  { label: 'Pikachu', query: 'Pikachu', game: 'pokemon' },
  { label: 'Luffy', query: 'Luffy', game: 'onepiece' },
  { label: 'Black Lotus', query: 'Black Lotus', game: 'magic' },
  { label: 'Dark Magician', query: 'Dark Magician', game: 'yugioh' },
]

function displayGameName(game) {
  if (!game) return ''
  if (game.name === 'Magic: The Gathering') return 'Magic'
  if (game.name === 'ONE PIECE Card Game') return 'One Piece'
  return game.name
}

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
    const timer = setTimeout(async () => {
      setLoading(true)
      try {
        const rows = await suggestCatalog({ q: clean, game: selectedGame, limit: 8 })
        if (!cancelled) setSuggestions(rows || [])
      } catch {
        if (!cancelled) setSuggestions([])
      } finally {
        if (!cancelled) setLoading(false)
      }
    }, 180)

    return () => {
      cancelled = true
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

  function runQuickSearch(item) {
    setSelectedGame(item.game)
    setQuery(item.query)
    setSuggestions([])
    router.push(scopedSearchHref(item.game, item.query))
  }

  const scopeLabel = selectedConfig ? displayGameName(selectedConfig) : 'Todos los TCG'

  return (
    <div className="v17-home-search-wrap">
      <div className="v17-home-search-console">
        <div className="v17-search-console-glow" aria-hidden="true" />

        <div className="v17-home-search-head">
          <div className="v17-home-search-title">
            <span><i aria-hidden="true" /> Buscador de catálogo</span>
            <strong>Encuentra la carta. Aterriza en la impresión correcta.</strong>
          </div>
          <div className="v17-home-search-flow" aria-label="Flujo de búsqueda">
            <small>Card</small><i aria-hidden="true" /><small>Print</small><i aria-hidden="true" /><small>Market</small>
          </div>
        </div>

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
              <i aria-hidden="true" />
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
                {displayGameName(game)}
              </button>
            ))}
          </div>

          <div className="v17-home-search-input">
            <span className="v17-search-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false">
                <circle cx="10.5" cy="10.5" r="5.75" />
                <path d="M15 15l4.5 4.5" />
              </svg>
            </span>
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

        <div className="v17-home-search-bottom">
          <div className="v17-home-search-examples" aria-label="Ejemplos de búsqueda">
            <span>Prueba con</span>
            {QUICK_SEARCHES.map((item) => (
              <button key={item.label} type="button" onClick={() => runQuickSearch(item)}>
                {item.label}
              </button>
            ))}
          </div>

          <div className="v17-home-search-meta" aria-label="Alcance de búsqueda">
            <small><i aria-hidden="true" /> Buscando en <strong>{scopeLabel}</strong></small>
            <button
              type="button"
              className="v17-home-hub-link"
              onClick={() => router.push(selectedGame ? `/games/${selectedGame}` : '/explorer')}
            >
              {selectedConfig ? `Abrir ${displayGameName(selectedConfig)} →` : 'Explorador avanzado →'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

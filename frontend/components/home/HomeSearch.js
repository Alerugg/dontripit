'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import SearchInput from '../search/SearchInput'
import { suggestCatalog } from '../../lib/catalog/client'
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

export default function HomeSearch() {
  const router = useRouter()
  const [query, setQuery] = useState('')
  const [suggestions, setSuggestions] = useState([])
  const [loading, setLoading] = useState(false)

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
        const rows = await suggestCatalog({ q: clean, limit: 8 })
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
  }, [query])

  function submit() {
    const clean = query.trim()
    if (!clean) {
      router.push('/explorer')
      return
    }
    router.push(`/explorer?q=${encodeURIComponent(clean)}&kind=card&view=grid`)
  }

  function selectSuggestion(item) {
    const href = suggestionHref(item)
    if (href) {
      router.push(href)
      return
    }
    const clean = String(item?.title || item?.name || query || '').trim()
    if (clean) router.push(`/explorer?q=${encodeURIComponent(clean)}&kind=card&view=grid`)
  }

  return (
    <div className="v5-home-search-wrap">
      <SearchInput
        type="search"
        value={query}
        onChange={setQuery}
        onSubmit={submit}
        suggestions={suggestions}
        suggestionsLoading={loading}
        onSuggestionSelect={selectSuggestion}
        placeholder="Busca Pikachu, Luffy, Black Lotus, Dark Magician…"
        variant="hero"
      />
      <div className="v5-home-search-meta" aria-label="Alcance de búsqueda">
        <button type="button" className="v5-home-search-scope" aria-pressed="true" aria-label="Buscar en todos los juegos" data-game="all">
          Todos los TCG
        </button>
        <small className="v4-search-hint">Enter busca todas las coincidencias · ↑↓ abre una sugerencia exacta</small>
      </div>
    </div>
  )
}

'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import CatalogSidebar from '../../components/catalog/CatalogSidebar'
import CatalogResults from '../../components/catalog/CatalogResults'
import StatePanel from '../../components/catalog/StatePanel'
import TopNav from '../../components/layout/TopNav'
import { useDebouncedValue } from '../../hooks/useDebouncedValue'
import { GAME_OPTIONS, RESULT_TYPE_OPTIONS, searchCatalog, suggestCatalog } from '../../lib/catalog/client'

const defaultGame = process.env.NEXT_PUBLIC_DEFAULT_GAME || ''

function resolveSuggestionHref(item) {
  if (item.type === 'print') return `/prints/${item.id}`
  if (item.type === 'card') return `/cards/${item.card_id || item.id}`
  return ''
}

export default function ExplorerPage() {
  const router = useRouter()

  const [inputValue, setInputValue] = useState('')
  const [submittedQuery, setSubmittedQuery] = useState('')
  const [game, setGame] = useState(defaultGame)
  const [type, setType] = useState('')
  const [view, setView] = useState('grid')

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const [suggestions, setSuggestions] = useState([])
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const debouncedInput = useDebouncedValue(inputValue.trim(), 250)

  useEffect(() => {
    if (!debouncedInput) {
      setSuggestions([])
      setSuggestionsLoading(false)
      return undefined
    }

    let cancelled = false

    const loadSuggestions = async () => {
      setSuggestionsLoading(true)

      try {
        const nextSuggestions = await suggestCatalog({ q: debouncedInput, game, limit: 8 })
        if (!cancelled) {
          setSuggestions(nextSuggestions)
        }
      } catch {
        if (!cancelled) {
          setSuggestions([])
        }
      } finally {
        if (!cancelled) {
          setSuggestionsLoading(false)
        }
      }
    }

    loadSuggestions()

    return () => {
      cancelled = true
    }
  }, [debouncedInput, game])

  useEffect(() => {
    if (!submittedQuery) {
      setItems([])
      setLoading(false)
      setError('')
      return undefined
    }

    let cancelled = false

    const loadSearchResults = async () => {
      setLoading(true)
      setError('')

      try {
        const nextItems = await searchCatalog({ q: submittedQuery, game, type, limit: 36, offset: 0 })
        if (!cancelled) {
          setItems(nextItems)
        }
      } catch (requestError) {
        if (!cancelled) {
          setItems([])
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadSearchResults()

    return () => {
      cancelled = true
    }
  }, [submittedQuery, game, type])

  const handleSubmitSearch = () => {
    const normalizedQuery = inputValue.trim()
    setSubmittedQuery(normalizedQuery)
  }

  const handleSelectSuggestion = (item) => {
    const title = item.title || item.name || ''
    const href = resolveSuggestionHref(item)

    setInputValue(title)
    setSuggestions([])

    if (href) {
      router.push(href)
      return
    }

    if (title) {
      setSubmittedQuery(title)
    }
  }

  return (
    <main>
      <TopNav />

      <section className="catalog-shell">
        <CatalogSidebar
          inputValue={inputValue}
          onInputChange={setInputValue}
          onSubmitSearch={handleSubmitSearch}
          suggestions={suggestions}
          suggestionsLoading={suggestionsLoading}
          onSuggestionSelect={handleSelectSuggestion}
          game={game}
          onGameChange={setGame}
          type={type}
          onTypeChange={setType}
          view={view}
          onViewChange={setView}
          gameOptions={GAME_OPTIONS}
          typeOptions={RESULT_TYPE_OPTIONS}
        />

        <div className="catalog-main">
          <header className="catalog-header panel">
            <p className="kicker">Explorer público multi-game</p>
            <h1>Descubre cartas, prints y sets</h1>
            <p>Usa sugerencias rápidas y pulsa Buscar para cargar resultados completos.</p>
          </header>

          {!submittedQuery && (
            <StatePanel
              title="Empieza a explorar Don’tRipIt"
              description="Escribe tu término y pulsa Buscar (o Enter) para consultar el catálogo multi-juego."
            />
          )}
          {submittedQuery && loading && <StatePanel title="Cargando catálogo" description="Estamos trayendo resultados actualizados para tu búsqueda." />}
          {submittedQuery && !loading && error && (
            <StatePanel
              title="No pudimos cargar el catálogo"
              description={error || 'Intenta de nuevo en unos segundos.'}
              error
            />
          )}
          {submittedQuery && !loading && !error && items.length === 0 && (
            <StatePanel
              title="Sin resultados por ahora"
              description="Prueba otro término o cambia el juego para seguir explorando cartas y variantes."
            />
          )}

          {!loading && !error && items.length > 0 && <CatalogResults items={items} view={view} />}
        </div>
      </section>
    </main>
  )
}

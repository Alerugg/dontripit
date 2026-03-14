'use client'

import { useEffect, useRef, useState } from 'react'
import CatalogSidebar from '../components/catalog/CatalogSidebar'
import CatalogResults from '../components/catalog/CatalogResults'
import StatePanel from '../components/catalog/StatePanel'
import TopNav from '../components/layout/TopNav'
import { GAME_OPTIONS, RESULT_TYPE_OPTIONS, searchCatalog } from '../lib/catalog/client'

const defaultGame = process.env.NEXT_PUBLIC_DEFAULT_GAME || ''

export default function ExplorerPage() {
  const [query, setQuery] = useState('')
  const [game, setGame] = useState(defaultGame)
  const [type, setType] = useState('')
  const [view, setView] = useState('grid')

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const normalizedQuery = query.trim()
  const isShortQuery = normalizedQuery.length > 0 && normalizedQuery.length <= 2

  const lastRequestKeyRef = useRef('')
  const requestCounterRef = useRef(0)

  useEffect(() => {
    if (!normalizedQuery) {
      setItems([])
      setError('')
      setLoading(false)
      lastRequestKeyRef.current = ''
      return undefined
    }

    const requestKey = JSON.stringify({ q: normalizedQuery, game, type })
    if (requestKey === lastRequestKeyRef.current) {
      return undefined
    }

    const requestId = ++requestCounterRef.current
    const timer = setTimeout(async () => {
      setLoading(true)
      setError('')

      try {
        const nextItems = await searchCatalog({ q: normalizedQuery, game, type, limit: isShortQuery ? 12 : 36, offset: 0 })
        if (requestId === requestCounterRef.current) {
          setItems(nextItems)
          lastRequestKeyRef.current = requestKey
        }
      } catch (requestError) {
        if (requestId === requestCounterRef.current) {
          setItems([])
          setError(requestError.message)
        }
      } finally {
        if (requestId === requestCounterRef.current) {
          setLoading(false)
        }
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [normalizedQuery, game, type, isShortQuery])

  return (
    <main>
      <TopNav />

      <section className="catalog-shell">
        <CatalogSidebar
          query={query}
          onQueryChange={setQuery}
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
            <p>Catálogo visual diseñado para evolucionar a colección, wishlist y marketplace.</p>
          </header>

          {!query.trim() && (
            <StatePanel
              title="Empieza a explorar Don’tRipIt"
              description="Escribe desde 1 carácter para consultar el catálogo multi-juego en tiempo real."
            />
          )}
          {normalizedQuery && !loading && !error && isShortQuery && (
            <StatePanel
              title="Resultados rápidos"
              description="Mostramos un set acotado y priorizado para autocomplete en queries cortas."
            />
          )}
          {query.trim() && loading && <StatePanel title="Cargando catálogo" description="Estamos trayendo resultados actualizados para tu búsqueda." />}
          {query.trim() && !loading && error && (
            <StatePanel
              title="No pudimos cargar el catálogo"
              description={error || 'Intenta de nuevo en unos segundos.'}
              error
            />
          )}
          {query.trim() && !loading && !error && items.length === 0 && (
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

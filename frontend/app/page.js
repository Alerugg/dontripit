'use client'

import { useEffect, useState } from 'react'
import CatalogSidebar from '../components/catalog/CatalogSidebar'
import CatalogResults from '../components/catalog/CatalogResults'
import StatePanel from '../components/catalog/StatePanel'
import TopNav from '../components/layout/TopNav'
import { GAME_OPTIONS, RESULT_TYPE_OPTIONS, searchCatalog } from '../lib/catalog/client'

const defaultGame = process.env.NEXT_PUBLIC_DEFAULT_GAME || ''

export default function ExplorerPage() {
  const [query, setQuery] = useState('charizard')
  const [game, setGame] = useState(defaultGame)
  const [type, setType] = useState('')
  const [view, setView] = useState('grid')

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const timer = setTimeout(async () => {
      setLoading(true)
      setError('')

      try {
        const nextItems = await searchCatalog({ q: query, game, type, limit: 36, offset: 0 })
        setItems(nextItems)
      } catch (requestError) {
        setItems([])
        setError(requestError.message)
      } finally {
        setLoading(false)
      }
    }, 250)

    return () => clearTimeout(timer)
  }, [query, game, type])

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

          {loading && <StatePanel title="Cargando catálogo" description="Estamos trayendo resultados actualizados para tu búsqueda." />}
          {!loading && error && (
            <StatePanel
              title="No pudimos cargar el catálogo"
              description={error || 'Intenta de nuevo en unos segundos.'}
              error
            />
          )}
          {!loading && !error && items.length === 0 && (
            <StatePanel
              title="Sin resultados por ahora"
              description="Prueba otro término o cambia el juego para seguir explorando cartas y variantes."
            />
          )}

          {!loading && !error && <CatalogResults items={items} view={view} />}
        </div>
      </section>
    </main>
  )
}

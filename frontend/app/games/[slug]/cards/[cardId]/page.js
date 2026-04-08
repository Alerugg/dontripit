'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import StatePanel from '../../../../../components/catalog/StatePanel'
import CardDetailLayout from '../../../../../components/cards/CardDetailLayout'
import { fetchCardById } from '../../../../../lib/catalog/client'
import { normalizeGameSlug } from '../../../../../lib/catalog/games'
import { getGameExplorerHref } from '../../../../../lib/catalog/routes'

export default function GameCardDetailPage({ params }) {
  const searchParams = useSearchParams()
  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadCard() {
      setLoading(true)
      setError('')

      const resolvedCardId = String(params.cardId || '').trim()

      try {
        if (!resolvedCardId) throw new Error('No se recibió cardId en la ruta.')
        const payload = await fetchCardById(resolvedCardId)
        const resolvedRouteSlug = normalizeGameSlug(params.slug || '')
        const payloadGameSlug = normalizeGameSlug(payload?.game || payload?.game_slug || '')

        if (resolvedRouteSlug && payloadGameSlug && resolvedRouteSlug !== payloadGameSlug) {
          throw new Error(`La carta ${resolvedCardId} pertenece a ${payloadGameSlug}, no a ${resolvedRouteSlug}.`)
        }

        if (!cancelled) setCard(payload)
      } catch (requestError) {
        if (!cancelled) {
          setCard(null)
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadCard()
    return () => {
      cancelled = true
    }
  }, [params.cardId])

  const fallbackHref = useMemo(() => {
    const target = new URLSearchParams()
    const q = searchParams.get('q') || ''
    const type = searchParams.get('type') || ''
    if (q) target.set('q', q)
    if (type) target.set('type', type)
    const query = target.toString()
    return `${getGameExplorerHref(params.slug)}${query ? `?${query}` : ''}`
  }, [params.slug, searchParams])

  return (
    <main>
      <TopNav />
      <section className="page-shell detail-shell">
        <div className="detail-actions">
          <button type="button" className="back-link" onClick={() => {
            if (window.history.length > 1) {
              window.history.back()
              return
            }
            window.location.assign(fallbackHref)
          }}>
            ← Volver a resultados
          </button>
          <Link href={fallbackHref} className="secondary-btn">Abrir explorador del juego</Link>
        </div>
        {loading && <StatePanel title="Cargando carta" description="Preparando carta, sets y variantes." />}
        {!loading && error && <StatePanel title="No pudimos cargar la carta" description={error} error />}
        {!loading && !error && card && (
          <CardDetailLayout
            card={card}
            routeGameSlug={params.slug}
            searchState={{ q: searchParams.get('q') || '', type: searchParams.get('type') || '' }}
          />
        )}
      </section>
    </main>
  )
}

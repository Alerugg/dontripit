'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import TopNav from '../../../../../components/layout/TopNav'
import StatePanel from '../../../../../components/catalog/StatePanel'
import CardDetailLayout from '../../../../../components/cards/CardDetailLayout'
import { fetchCardById } from '../../../../../lib/catalog/client'
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

      try {
        const payload = await fetchCardById(params.cardId)
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
        {!loading && !error && card && <CardDetailLayout card={card} />}
      </section>
    </main>
  )
}

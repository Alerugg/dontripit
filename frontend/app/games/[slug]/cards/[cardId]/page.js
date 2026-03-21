'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../../../components/layout/TopNav'
import StatePanel from '../../../../../components/catalog/StatePanel'
import CardDetailLayout from '../../../../../components/cards/CardDetailLayout'
import { fetchCardById } from '../../../../../lib/catalog/client'
import { getGameExplorerHref } from '../../../../../lib/catalog/routes'

export default function GameCardDetailPage({ params }) {
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

  return (
    <main>
      <TopNav />
      <section className="detail-shell">
        <Link href={getGameExplorerHref(params.slug)} className="back-link">← Volver al explorer</Link>
        {loading && <StatePanel title="Cargando carta" description="Preparando carta, sets y variantes." />}
        {!loading && error && <StatePanel title="No pudimos cargar la carta" description={error} error />}
        {!loading && !error && card && <CardDetailLayout card={card} />}
      </section>
    </main>
  )
}

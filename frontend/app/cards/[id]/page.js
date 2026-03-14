'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { fetchCardById } from '../../../lib/apiClient'

export default function CardDetailPage({ params }) {
  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetchCardById(params.id)
      .then(setCard)
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [params.id])

  return (
    <main className="explorer-page">
      <Link href="/" className="ghost-btn">← Volver al explorer</Link>
      {loading && <section className="panel state-panel">Cargando carta...</section>}
      {!loading && error && <section className="panel state-panel error">Error: {error}</section>}

      {!loading && !error && card && (
        <section className="panel detail-layout">
          <div className="detail-image-wrap">
            {card.primary_image_url ? <img src={card.primary_image_url} alt={card.name} className="detail-image" /> : <div className="result-image-placeholder">Sin imagen</div>}
          </div>
          <div>
            <h1>{card.name}</h1>
            <p className="subtle">Juego: {card.game}</p>

            <h2>Sets</h2>
            <div className="chip-list">
              {(card.sets || []).map((set) => <span className="chip" key={set.id}>{set.code} · {set.name}</span>)}
            </div>

            <h2>Prints</h2>
            <div className="related-list">
              {(card.prints || []).map((print) => (
                <Link key={print.id} href={`/prints/${print.id}`} className="related-item">
                  <strong>{print.set_code} #{print.collector_number || '-'}</strong>
                  <span>{print.variant || 'Standard'} · {print.rarity || 'N/A'}</span>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}
    </main>
  )
}

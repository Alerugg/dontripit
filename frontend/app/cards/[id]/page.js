'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import AppShell from '../../../components/AppShell'
import { fetchCardById } from '../../../lib/apiClient'

function externalIdPairs(externalIds) {
  return Object.entries(externalIds || {}).filter(([, value]) => value)
}

export default function CardDetailPage({ params }) {
  const [card, setCard] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    fetchCardById(params.id)
      .then(setCard)
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [params.id])

  const externalIds = useMemo(() => externalIdPairs(card?.external_ids), [card])

  return (
    <AppShell>
      <main className="explorer-page">
        <Link href="/" className="ghost-btn">← Volver al explorer</Link>
        {loading && <section className="panel state-panel">Cargando carta...</section>}
        {!loading && error && <section className="panel state-panel error">{error}</section>}

        {!loading && !error && card && (
          <section className="panel detail-layout">
            <div className="detail-image-wrap">
              {card.primary_image_url ? <img src={card.primary_image_url} alt={card.name} className="detail-image" /> : <div className="result-image-placeholder">Sin imagen</div>}
            </div>

            <div className="detail-main">
              <h1>{card.name}</h1>
              <p className="subtle">Juego: {card.game || card.game_slug || 'N/A'}</p>

              <div className="detail-summary-grid">
                <article className="panel soft">
                  <h3>Resumen</h3>
                  <p>Carta base del catálogo preparada para colección, binder y comparación de prints/variantes.</p>
                </article>
                <article className="panel soft">
                  <h3>Sets asociados</h3>
                  <div className="chip-list">
                    {(card.sets || []).map((set) => <span className="chip" key={set.id}>{set.code} · {set.name}</span>)}
                    {(!card.sets || card.sets.length === 0) && <span className="subtle">Sin sets asociados</span>}
                  </div>
                </article>
              </div>

              <section>
                <h2>External IDs</h2>
                <div className="meta-list">
                  {externalIds.map(([key, value]) => <p key={key}><strong>{key}:</strong> {String(value)}</p>)}
                  {externalIds.length === 0 && <p>Sin identificadores externos registrados.</p>}
                </div>
              </section>

              <section>
                <h2>Prints / Variantes</h2>
                <div className="related-list">
                  {(card.prints || []).map((print) => (
                    <Link key={print.id} href={`/prints/${print.id}`} className="related-item">
                      <strong>{print.set_code || 'SET'} #{print.collector_number || '-'}</strong>
                      <span>{print.variant || 'Standard'} · {print.rarity || 'N/A'} · {print.language || 'N/A'}</span>
                    </Link>
                  ))}
                  {(!card.prints || card.prints.length === 0) && <p className="subtle">No hay prints disponibles.</p>}
                </div>
              </section>
            </div>
          </section>
        )}
      </main>
    </AppShell>
  )
}

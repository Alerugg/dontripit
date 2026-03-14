'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { fetchPrintById } from '../../../lib/apiClient'

export default function PrintDetailPage({ params }) {
  const [printDetail, setPrintDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    fetchPrintById(params.id)
      .then(setPrintDetail)
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [params.id])

  return (
    <main className="explorer-page">
      <Link href="/" className="ghost-btn">← Volver al explorer</Link>
      {loading && <section className="panel state-panel">Cargando print...</section>}
      {!loading && error && <section className="panel state-panel error">Error: {error}</section>}

      {!loading && !error && printDetail && (
        <section className="panel detail-layout">
          <div className="detail-image-wrap">
            {printDetail.primary_image_url ? <img src={printDetail.primary_image_url} alt={printDetail.title} className="detail-image" /> : <div className="result-image-placeholder">Sin imagen</div>}
          </div>
          <div>
            <h1>{printDetail.card?.name || printDetail.title}</h1>
            <p className="subtle">{printDetail.set_name} ({printDetail.set_code})</p>

            <div className="meta-list">
              <p><strong>Juego:</strong> {printDetail.game}</p>
              <p><strong>Collector:</strong> {printDetail.collector_number || '-'}</p>
              <p><strong>Rarity:</strong> {printDetail.rarity || '-'}</p>
              <p><strong>Variant:</strong> {printDetail.variant || '-'}</p>
              <p><strong>Language:</strong> {printDetail.language || '-'}</p>
            </div>

            {printDetail.card?.id && (
              <Link href={`/cards/${printDetail.card.id}`} className="primary-btn">Ver carta base</Link>
            )}
          </div>
        </section>
      )}
    </main>
  )
}

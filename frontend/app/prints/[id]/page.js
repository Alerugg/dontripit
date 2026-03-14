'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import AppShell from '../../../components/AppShell'
import { fetchPrintById } from '../../../lib/apiClient'

function externalIdPairs(externalIds) {
  return Object.entries(externalIds || {}).filter(([, value]) => value)
}

export default function PrintDetailPage({ params }) {
  const [printDetail, setPrintDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    fetchPrintById(params.id)
      .then(setPrintDetail)
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [params.id])

  const externalIds = useMemo(() => externalIdPairs(printDetail?.external_ids), [printDetail])

  return (
    <AppShell>
      <main className="explorer-page">
        <Link href="/" className="ghost-btn">← Volver al explorer</Link>
        {loading && <section className="panel state-panel">Cargando print...</section>}
        {!loading && error && <section className="panel state-panel error">{error}</section>}

        {!loading && !error && printDetail && (
          <section className="panel detail-layout">
            <div className="detail-image-wrap">
              {printDetail.primary_image_url ? <img src={printDetail.primary_image_url} alt={printDetail.title || printDetail.card?.name} className="detail-image" /> : <div className="result-image-placeholder">Sin imagen</div>}
            </div>

            <div className="detail-main">
              <h1>{printDetail.card?.name || printDetail.title || 'Print detail'}</h1>
              <p className="subtle">{printDetail.set_name || 'Set desconocido'} ({printDetail.set_code || '-'})</p>

              <section className="meta-list">
                <p><strong>Juego:</strong> {printDetail.game || '-'}</p>
                <p><strong>Collector number:</strong> {printDetail.collector_number || '-'}</p>
                <p><strong>Rarity:</strong> {printDetail.rarity || '-'}</p>
                <p><strong>Variant:</strong> {printDetail.variant || '-'}</p>
                <p><strong>Language:</strong> {printDetail.language || '-'}</p>
                <p><strong>Foil:</strong> {String(printDetail.is_foil)}</p>
              </section>

              <section>
                <h2>Identifiers</h2>
                <div className="meta-list">
                  {externalIds.map(([key, value]) => <p key={key}><strong>{key}:</strong> {String(value)}</p>)}
                  {externalIds.length === 0 && <p>Sin external IDs para este print.</p>}
                </div>
              </section>

              {printDetail.card?.id && (
                <Link href={`/cards/${printDetail.card.id}`} className="primary-btn">Ver carta base y variantes</Link>
              )}
            </div>
          </section>
        )}
      </main>
    </AppShell>
  )
}

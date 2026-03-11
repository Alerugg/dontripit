'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { fetchCardDetail, fetchPrintDetail } from '../../../../lib/apiClient'
import { readStoredAuth } from '../../../../lib/apiKeyStorage'

export default function DetailPage({ params }) {
  const { type, id } = params
  const [apiKey, setApiKey] = useState('')
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const auth = readStoredAuth()
    setApiKey(auth.apiKey)
  }, [])

  useEffect(() => {
    if (!apiKey.trim()) {
      setError('Falta API key. Vuelve al explorer y genera/guarda una clave válida.')
      setLoading(false)
      return
    }

    setLoading(true)
    const request = type === 'card' ? fetchCardDetail(id, apiKey) : fetchPrintDetail(id, apiKey)
    request.then((payload) => setDetail(payload)).catch((requestError) => setError(requestError.message)).finally(() => setLoading(false))
  }, [type, id, apiKey])

  return (
    <main className="catalog-page">
      <Link href="/explorer" className="ghost-btn">← Volver al catálogo</Link>
      {loading && <p className="hint">Cargando detalle...</p>}
      {error && <p className="banner error">{error}</p>}

      {!loading && detail && type === 'card' && (
        <section className="detail-layout panel">
          <div className="detail-media">
            {detail.primary_image_url ? <img src={detail.primary_image_url} alt={detail.name} /> : <span>Sin imagen</span>}
          </div>
          <div>
            <h1>{detail.name}</h1>
            <p className="hint">Juego: {detail.game_slug}</p>
            <p className="hint">External IDs: {Object.entries(detail.external_ids || {}).filter(([, value]) => value).map(([key, value]) => `${key}:${value}`).join(' · ') || 'No disponible'}</p>
            <h2>Sets relacionados</h2>
            <div className="meta-chips">
              {(detail.sets || []).map((setRow) => <span key={setRow.id} className="chip">{setRow.code} · {setRow.name}</span>)}
            </div>
            <h2>Prints relacionados</h2>
            <div className="related-grid">
              {(detail.prints || []).map((print) => (
                <Link key={print.id} href={`/explorer/print/${print.id}`} className="related-card">
                  <div className="thumb-xs">{print.primary_image_url ? <img src={print.primary_image_url} alt={detail.name} /> : <span>TCG</span>}</div>
                  <div>
                    <strong>{print.set_code} #{print.collector_number || '-'}</strong>
                    <small>{print.variant || 'Standard'} · {print.rarity || 'N/A'} · {print.language || 'N/A'}</small>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}

      {!loading && detail && type === 'print' && (
        <section className="detail-layout panel">
          <div className="detail-media">
            {detail.primary_image_url ? <img src={detail.primary_image_url} alt={detail.title} /> : <span>Sin imagen</span>}
          </div>
          <div>
            <h1>{detail.card?.name || detail.title}</h1>
            <p className="hint">Juego: {detail.game}</p>
            <div className="detail-meta">
              <p><strong>Set:</strong> {detail.set_name} ({detail.set_code})</p>
              <p><strong>Collector number:</strong> {detail.collector_number || '-'}</p>
              <p><strong>Variant:</strong> {detail.variant || '-'}</p>
              <p><strong>Rarity:</strong> {detail.rarity || '-'}</p>
              <p><strong>Language:</strong> {detail.language || '-'}</p>
              <p><strong>Foil:</strong> {String(detail.is_foil)}</p>
              <p><strong>External IDs:</strong> {Object.entries(detail.external_ids || {}).filter(([, value]) => value).map(([key, value]) => `${key}:${value}`).join(' · ') || 'No disponible'}</p>
            </div>
            <div className="related-grid">
              <Link href={`/explorer/card/${detail.card?.id}`} className="related-card">
                <div>
                  <strong>Ver card base</strong>
                  <small>{detail.card?.name}</small>
                </div>
              </Link>
            </div>
          </div>
        </section>
      )}
    </main>
  )
}

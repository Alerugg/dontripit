'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import StatePanel from '../../../components/catalog/StatePanel'
import { fetchPrintById } from '../../../lib/catalog/client'

function MetaLine({ label, value }) {
  if (!value && value !== false) return null
  return <p><strong>{label}:</strong> {String(value)}</p>
}

export default function PrintDetailPage({ params }) {
  const [printDetail, setPrintDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadPrint() {
      setLoading(true)
      setError('')

      try {
        const payload = await fetchPrintById(params.id)
        if (!cancelled) setPrintDetail(payload)
      } catch (requestError) {
        if (!cancelled) {
          setPrintDetail(null)
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadPrint()
    return () => {
      cancelled = true
    }
  }, [params.id])

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        <Link href={`/cards/${printDetail?.card?.id || ''}`} className="back-link">← Volver a la carta</Link>

        {loading && <StatePanel title="Cargando print" description="Traemos metadata de edición y variantes." />}
        {!loading && error && <StatePanel title="No pudimos cargar el print" description={error} error />}

        {!loading && !error && printDetail && (
          <article className="panel detail-page">
            <div className="detail-media">
              {printDetail.primary_image_url ? (
                <img src={printDetail.primary_image_url} alt={printDetail.card?.name || 'Print'} className="detail-image" />
              ) : (
                <div className="catalog-placeholder">Sin imagen</div>
              )}
            </div>

            <div className="detail-content">
              <p className="kicker">Print detail</p>
              <h1>{printDetail.card?.name || 'Carta'}</h1>
              <p className="meta-game">{printDetail.game || printDetail.card?.game}</p>

              <section className="meta-grid panel-soft">
                <MetaLine label="Set" value={printDetail.set_name} />
                <MetaLine label="Set Code" value={printDetail.set_code} />
                <MetaLine label="Collector" value={printDetail.collector_number} />
                <MetaLine label="Rarity" value={printDetail.rarity} />
                <MetaLine label="Variant" value={printDetail.variant} />
                <MetaLine label="Foil" value={printDetail.foil} />
                <MetaLine label="Language" value={printDetail.language} />
              </section>

              <section className="panel-soft identifiers">
                <h2>Identifiers & external IDs</h2>
                <MetaLine label="Print ID" value={printDetail.id} />
                <MetaLine label="Scryfall" value={printDetail.external_ids?.scryfall_id} />
                <MetaLine label="TCGPlayer" value={printDetail.external_ids?.tcgplayer_id} />
                <MetaLine label="Cardmarket" value={printDetail.external_ids?.cardmarket_id} />
              </section>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}

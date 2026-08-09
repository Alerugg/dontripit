'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import FallbackImage from '../../../components/common/FallbackImage'
import StatePanel from '../../../components/catalog/StatePanel'
import LibraryActions from '../../../components/library/LibraryActions'
import { fetchPrintById } from '../../../lib/catalog/client'
import { getCardHref, getGameExplorerHref } from '../../../lib/catalog/routes'

function MetaLine({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return <p><strong>{label}:</strong> {String(value)}</p>
}

function PriceBlock({ price }) {
  if (!price) {
    return (
      <section className="panel-soft identifiers">
        <p className="eyebrow">Precio</p>
        <h2>Sin precio verificado todavía</h2>
        <p className="detail-meta">No mostramos una estimación si no tenemos una fuente y una fecha asociadas a esta edición exacta.</p>
      </section>
    )
  }
  const formatted = new Intl.NumberFormat('es-ES', {
    style: 'currency',
    currency: price.currency || 'EUR',
    maximumFractionDigits: 2,
  }).format(Number(price.value || 0))
  return (
    <section className="panel-soft identifiers">
      <p className="eyebrow">Precio observado</p>
      <h2>{formatted}</h2>
      <p className="detail-meta">{price.source ? `Fuente: ${price.source}` : 'Fuente registrada'}{price.as_of ? ` · ${new Date(price.as_of).toLocaleDateString('es-ES')}` : ''}</p>
    </section>
  )
}

export default function PrintDetailPage({ params }) {
  const { id } = use(params)
  const [printDetail, setPrintDetail] = useState(null)
  const [price, setPrice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadPrint() {
      setLoading(true)
      setError('')
      try {
        const [payload, priceResponse] = await Promise.all([
          fetchPrintById(id),
          fetch(`/api/prices/print/${id}`, { cache: 'no-store' }).then((response) => response.ok ? response.json() : { price: null }).catch(() => ({ price: null })),
        ])
        if (!cancelled) {
          setPrintDetail(payload)
          setPrice(priceResponse?.price || null)
        }
      } catch (requestError) {
        if (!cancelled) {
          setPrintDetail(null)
          setPrice(null)
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
  }, [id])

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        <Link href={getCardHref(printDetail?.game || printDetail?.card?.game, printDetail?.card?.id || '')} className="back-link">← Volver a la carta</Link>

        {loading && <StatePanel title="Cargando edición" description="Preparando la versión física exacta." />}
        {!loading && error && <StatePanel title="No pudimos cargar esta edición" description={error} error />}

        {!loading && !error && printDetail && (
          <article className="panel detail-page">
            <div className="detail-media-column">
              <div className="detail-media detail-media-card">
                <FallbackImage
                  src={printDetail.primary_image_url}
                  alt={printDetail.card?.name || 'Carta'}
                  className="detail-image"
                  placeholderClassName="catalog-placeholder image-fallback"
                  label={printDetail.game || printDetail.card?.game || 'TCG'}
                />
              </div>
              <PriceBlock price={price} />
            </div>

            <div className="detail-content">
              <nav className="detail-breadcrumbs" aria-label="breadcrumb">
                <Link href={getGameExplorerHref(printDetail.game || printDetail.card?.game || 'pokemon')}>{printDetail.game || printDetail.card?.game || 'TCG'}</Link>
                <span>→</span>
                <span>{printDetail.set_name || printDetail.set_code || 'Colección'}</span>
                <span>→</span>
                <Link href={getCardHref(printDetail.game || printDetail.card?.game, printDetail.card?.id || '')}>{printDetail.card?.name || 'Carta'}</Link>
              </nav>

              <div className="detail-title-block">
                <p className="eyebrow">Edición exacta</p>
                <h1>{printDetail.card?.name || 'Carta'}</h1>
                <p className="detail-intro">{[printDetail.set_code, printDetail.collector_number ? `#${printDetail.collector_number}` : null, printDetail.language?.toUpperCase(), printDetail.rarity].filter(Boolean).join(' · ')}</p>
              </div>

              <LibraryActions printId={printDetail.id} />

              <section className="meta-grid panel-soft">
                <MetaLine label="Set" value={printDetail.set_name} />
                <MetaLine label="Código" value={printDetail.set_code} />
                <MetaLine label="Número" value={printDetail.collector_number} />
                <MetaLine label="Rareza" value={printDetail.rarity} />
                <MetaLine label="Variante" value={printDetail.variant} />
                <MetaLine label="Foil" value={printDetail.foil ? 'Sí' : 'No'} />
                <MetaLine label="Idioma" value={printDetail.language?.toUpperCase()} />
              </section>

              <section className="panel-soft identifiers">
                <p className="eyebrow">Por qué importa</p>
                <h2>Esta es la edición que guardas</h2>
                <p className="detail-meta">Don’tRipIt diferencia cada versión física para que tu colección y wishlist no mezclen artes, finishes o reimpresiones distintas.</p>
              </section>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}

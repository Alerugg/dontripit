'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import FallbackImage from '../../../components/common/FallbackImage'
import StatePanel from '../../../components/catalog/StatePanel'
import LibraryActions from '../../../components/library/LibraryActions'
import { fetchPrintById } from '../../../lib/catalog/client'
import { getCardHref, getGameExplorerHref, getSetHref } from '../../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../../lib/catalog/games'
import './PrintDetailPage.css'

function MetaLine({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return <p><strong>{label}:</strong> {String(value)}</p>
}

function money(value, currency = 'EUR') {
  if (value === null || value === undefined) return '—'
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value))
  } catch {
    return `${value} ${currency}`
  }
}

function PriceMetric({ label, value, currency, featured = false }) {
  return (
    <div className={`ux-price-metric ${featured ? 'is-featured' : ''}`}>
      <span>{label}</span>
      <strong>{money(value, currency)}</strong>
    </div>
  )
}

function PriceBlock({ price, cardmarket }) {
  if (!price) {
    return (
      <section className="panel-soft identifiers ux-price-panel">
        <p className="eyebrow">Cardmarket</p>
        <h2>Sin Price Guide actual</h2>
        <p className="detail-meta">No reutilizamos el precio de otra edición. Esta versión solo recibe precio cuando Cardmarket aporta datos para su contraparte exacta.</p>
        {cardmarket?.url ? (
          <a
            href={cardmarket.url}
            target="_blank"
            rel="noopener noreferrer sponsored"
            className="dri-btn"
          >
            Ver esta versión en Cardmarket ↗
          </a>
        ) : null}
      </section>
    )
  }

  const currency = price.currency || 'EUR'
  const hasConservative = price.conservative !== null && price.conservative !== undefined

  return (
    <section className="panel-soft identifiers ux-price-panel">
      <div className="ux-price-heading">
        <div>
          <p className="eyebrow">Cardmarket</p>
          <h2>{hasConservative ? 'Valor conservador' : 'Precio disponible'}</h2>
        </div>
        {hasConservative ? <strong className="ux-price-main">{money(price.conservative, currency)}</strong> : null}
      </div>

      <div className="ux-price-grid">
        <PriceMetric label="Mínimo" value={price.minimum} currency={currency} />
        <PriceMetric label="Conservador" value={price.conservative} currency={currency} featured />
        <PriceMetric label="Tendencia" value={price.trend} currency={currency} />
        <PriceMetric label="Media" value={price.average} currency={currency} />
      </div>

      <p className="detail-meta ux-price-explainer">
        {hasConservative
          ? 'El valor conservador es la referencia que usamos para el portfolio cuando Cardmarket dispone de la métrica compatible con esta edición.'
          : 'Este snapshot no contiene una métrica conservadora; por eso no entra en el valor de tu portfolio.'}
      </p>
      <p className="detail-meta ux-price-explainer">Las métricas respetan el acabado físico de la carta: Low Price EX+ para la referencia conservadora no foil y Foil Low cuando corresponde a una edición foil.</p>
      <p className="detail-meta">Fuente: {price.source || 'Cardmarket'}{price.as_of ? ` · actualizado ${new Date(price.as_of).toLocaleDateString('es-ES')}` : ''}</p>
      {cardmarket?.url ? (
        <a
          href={cardmarket.url}
          target="_blank"
          rel="noopener noreferrer sponsored"
          className="dri-btn"
        >
          Comprar en Cardmarket ↗
        </a>
      ) : null}
    </section>
  )
}

export default function PrintDetailPage({ params }) {
  const { id } = use(params)
  const [printDetail, setPrintDetail] = useState(null)
  const [price, setPrice] = useState(null)
  const [cardmarket, setCardmarket] = useState(null)
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
          fetch(`/api/prices/print/${id}`, { cache: 'no-store' }).then((response) => response.ok ? response.json() : { price: null, cardmarket: null }).catch(() => ({ price: null, cardmarket: null })),
        ])
        if (!cancelled) {
          setPrintDetail(payload)
          setPrice(priceResponse?.price || null)
          setCardmarket(priceResponse?.cardmarket || priceResponse?.price?.cardmarket || null)
        }
      } catch (requestError) {
        if (!cancelled) {
          setPrintDetail(null)
          setPrice(null)
          setCardmarket(null)
          setError(requestError.message)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadPrint()
    return () => { cancelled = true }
  }, [id])

  const rawGameSlug = printDetail?.game || printDetail?.card?.game || 'pokemon'
  const gameSlug = normalizeGameSlug(rawGameSlug)
  const gameLabel = getGameConfig(gameSlug)?.name || rawGameSlug
  const cardId = printDetail?.card?.id || ''
  const cardHref = getCardHref(gameSlug, cardId)
  const setHref = printDetail?.set_code ? getSetHref(gameSlug, printDetail.set_code) : getGameExplorerHref(gameSlug)
  const finishLabel = printDetail?.foil || printDetail?.is_foil ? 'Foil' : 'No foil'
  const variantLabel = printDetail?.variant && printDetail.variant !== 'default' ? printDetail.variant : null

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        {loading && <StatePanel title="Cargando versión" description="Preparando la edición física exacta y su mercado." />}
        {!loading && error && <StatePanel title="No pudimos cargar esta versión" description={error} error />}

        {!loading && !error && printDetail && (
          <article className="detail-page">
            <div className="detail-media-column">
              <div className="detail-media detail-media-card">
                <FallbackImage
                  src={printDetail.primary_image_url}
                  alt={printDetail.card?.name || 'Carta'}
                  className="detail-image"
                  placeholderClassName="catalog-placeholder image-fallback"
                  label={gameLabel}
                />
              </div>
              <PriceBlock price={price} cardmarket={cardmarket} />
            </div>

            <div className="detail-content">
              <nav className="detail-breadcrumbs" aria-label="breadcrumb">
                <Link href={getGameExplorerHref(gameSlug)}>{gameLabel}</Link>
                <span>→</span>
                <Link href={setHref}>{printDetail.set_name || printDetail.set_code || 'Set'}</Link>
                <span>→</span>
                <Link href={cardHref}>{printDetail.card?.name || 'Carta'}</Link>
              </nav>

              <div className="dri-exact-head">
                <div className="dri-exact-head-copy detail-title-block">
                  <p className="eyebrow">Versión exacta</p>
                  <h1>{printDetail.card?.name || 'Carta'}</h1>
                  <p className="detail-intro">
                    {[printDetail.set_code?.toUpperCase?.() || printDetail.set_code, printDetail.collector_number ? `#${printDetail.collector_number}` : null, printDetail.language?.toUpperCase(), printDetail.rarity, finishLabel, variantLabel].filter(Boolean).join(' · ')}
                  </p>
                </div>
                <span className="dri-exact-status">Identidad física</span>
              </div>

              <section className="dri-exact-actions">
                <div className="dri-exact-actions-copy">
                  <p className="eyebrow">Tu colección</p>
                  <h2>¿Esta es la versión correcta?</h2>
                  <p>Guárdala aquí. Las acciones se aplican a esta edición concreta, no solo al nombre de la carta.</p>
                </div>
                <LibraryActions printId={printDetail.id} />
              </section>

              <section className="meta-grid panel-soft">
                <MetaLine label="Set" value={printDetail.set_name} />
                <MetaLine label="Código" value={printDetail.set_code?.toUpperCase?.() || printDetail.set_code} />
                <MetaLine label="Número" value={printDetail.collector_number} />
                <MetaLine label="Rareza" value={printDetail.rarity} />
                <MetaLine label="Variante" value={variantLabel} />
                <MetaLine label="Acabado" value={finishLabel} />
                <MetaLine label="Idioma" value={printDetail.language?.toUpperCase()} />
                {cardmarket?.id_product ? <MetaLine label="Cardmarket idProduct" value={cardmarket.id_product} /> : null}
              </section>

              <div className="dri-exact-navigation">
                <Link href={cardHref} className="dri-btn dri-btn-ghost">← Ver las demás versiones</Link>
                <Link href={setHref} className="dri-btn dri-btn-ghost">Ver el set completo</Link>
                {cardmarket?.url ? (
                  <a href={cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn">Cardmarket ↗</a>
                ) : null}
              </div>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}

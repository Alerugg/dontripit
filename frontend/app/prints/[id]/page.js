'use client'

import { use, useEffect, useState } from 'react'
import Link from 'next/link'
import TopNav from '../../../components/layout/TopNav'
import FallbackImage from '../../../components/common/FallbackImage'
import StatePanel from '../../../components/catalog/StatePanel'
import LibraryActions from '../../../components/library/LibraryActions'
import { fetchPrintById, fetchPrintPhysicalReleases } from '../../../lib/catalog/client'
import { getCardHref, getGameExplorerHref, getSetHref } from '../../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../../lib/catalog/games'
import './PrintDetailPage.css'

function MetaLine({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return <div className="dri-version-fact"><span>{label}</span><strong>{String(value)}</strong></div>
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

function releaseDisplayCode(release) {
  if (release?.code) return String(release.code).toUpperCase()
  const match = String(release?.name || '').match(/\[([^\]]+)\]/)
  return match?.[1] ? match[1].toUpperCase() : null
}

function compactCode(value) {
  return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
}

function friendlyVariant(value) {
  const raw = String(value || '').trim()
  if (!raw || ['default', 'base'].includes(raw.toLowerCase())) return null
  if (/^rarity-/i.test(raw)) return null
  return raw.replace(/[-_]+/g, ' ')
}

function PriceMetric({ label, value, currency, featured = false }) {
  if (value === null || value === undefined) return null
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
        <p className="eyebrow">Mercado</p>
        <h2>Cardmarket</h2>
        <p className="detail-meta"><strong>Sin Price Guide actual.</strong> No reutilizamos el precio de otra edición: solo mostramos la contraparte exacta cuando existe en Cardmarket.</p>
        {cardmarket?.url ? (
          <a href={cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn">
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
          <p className="eyebrow">Mercado</p>
          <h2>Cardmarket</h2>
        </div>
        {hasConservative ? <strong className="ux-price-main">{money(price.conservative, currency)}</strong> : null}
      </div>

      <div className="ux-price-grid">
        <PriceMetric label="Mínimo" value={price.minimum} currency={currency} />
        <PriceMetric label="Conservador" value={price.conservative} currency={currency} featured />
        <PriceMetric label="Tendencia" value={price.trend} currency={currency} />
        <PriceMetric label="Media" value={price.average} currency={currency} />
      </div>

      <p className="detail-meta">{price.as_of ? `Actualizado ${new Date(price.as_of).toLocaleDateString('es-ES')}` : 'Precio de la versión comercial vinculada.'}</p>
      <details className="dri-technical dri-price-method">
        <summary>Cómo se calcula</summary>
        <p className="detail-meta">El valor conservador usa Low Price EX+ para versiones no foil y Foil Low para versiones foil cuando Cardmarket publica esa métrica. No reutilizamos el precio de otra edición.</p>
      </details>
      {cardmarket?.url ? (
        <a href={cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn">
          Ver esta versión en Cardmarket ↗
        </a>
      ) : null}
    </section>
  )
}

export default function PrintDetailPage({ params }) {
  const { id } = use(params)
  const [printDetail, setPrintDetail] = useState(null)
  const [physicalReleases, setPhysicalReleases] = useState([])
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
        const [payload, releaseResponse, priceResponse] = await Promise.all([
          fetchPrintById(id),
          fetchPrintPhysicalReleases(id).catch(() => ({ physical_releases: [] })),
          fetch(`/api/prices/print/${id}`, { cache: 'no-store' }).then((response) => response.ok ? response.json() : { price: null, cardmarket: null }).catch(() => ({ price: null, cardmarket: null })),
        ])
        if (!cancelled) {
          setPrintDetail(payload)
          setPhysicalReleases(Array.isArray(releaseResponse?.physical_releases) ? releaseResponse.physical_releases : [])
          setPrice(priceResponse?.price || null)
          setCardmarket(priceResponse?.cardmarket || priceResponse?.price?.cardmarket || null)
        }
      } catch (requestError) {
        if (!cancelled) {
          setPrintDetail(null)
          setPhysicalReleases([])
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
  const originSetHref = printDetail?.set_code ? getSetHref(gameSlug, printDetail.set_code) : getGameExplorerHref(gameSlug)
  const finishLabel = printDetail?.foil || printDetail?.is_foil ? 'Foil' : 'No foil'
  const variantLabel = friendlyVariant(printDetail?.variant)
  const primaryRelease = physicalReleases[0] || null
  const physicalReleaseCode = releaseDisplayCode(primaryRelease)
  const physicalReleaseHref = physicalReleaseCode ? getSetHref(gameSlug, physicalReleaseCode.toLowerCase()) : null
  const originCode = printDetail?.set_code?.toUpperCase?.() || printDetail?.set_code || null
  const releaseDiffersFromOrigin = physicalReleaseCode && originCode && compactCode(physicalReleaseCode) !== compactCode(originCode)
  const versionCode = physicalReleaseCode || originCode

  return (
    <main>
      <TopNav />

      <section className="detail-shell">
        {loading && <StatePanel title="Cargando versión" description="Preparando la edición física exacta y su mercado." />}
        {!loading && error && <StatePanel title="No pudimos cargar esta versión" description={error} error />}

        {!loading && !error && printDetail && (
          <article className="detail-page">
            <div className="detail-media-column">
              <div className="detail-media detail-media-card dri-print-media">
                <FallbackImage
                  src={printDetail.primary_image_url}
                  alt={printDetail.card?.name || printDetail.title || 'Nombre no disponible'}
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
                <Link href={cardHref}>{printDetail.card?.name || printDetail.title || 'Carta'}</Link>
                <span>→</span>
                <strong>{versionCode || printDetail.collector_number || 'Versión'}</strong>
              </nav>

              <div className="dri-exact-head">
                <div className="dri-exact-head-copy detail-title-block">
                  <p className="eyebrow">Versión física</p>
                  <h1>{printDetail.card?.name || printDetail.title || 'Nombre no disponible'}</h1>
                  <p className="detail-intro">
                    {[versionCode, printDetail.collector_number, printDetail.language?.toUpperCase(), printDetail.rarity, finishLabel].filter(Boolean).join(' · ')}
                  </p>
                  {releaseDiffersFromOrigin ? (
                    <p className="detail-meta">Publicada físicamente en <strong>{physicalReleaseCode}</strong>; su carta/set de origen usa <strong>{originCode}</strong>.</p>
                  ) : null}
                </div>
              </div>

              <section className="dri-version-summary panel-soft">
                <div className="dri-version-summary-head">
                  <div>
                    <p className="eyebrow">Esta versión</p>
                    <h2>Información esencial</h2>
                  </div>
                  <span className="dri-language-pill">{printDetail.language?.toUpperCase() || '—'}</span>
                </div>
                <div className="dri-version-facts">
                  <MetaLine label="Edición" value={primaryRelease?.name || printDetail.set_name} />
                  <MetaLine label="Código" value={versionCode} />
                  <MetaLine label="Número" value={printDetail.collector_number} />
                  <MetaLine label="Rareza" value={printDetail.rarity} />
                  <MetaLine label="Acabado" value={finishLabel} />
                  <MetaLine label="Idioma físico" value={printDetail.language?.toUpperCase()} />
                </div>
              </section>

              <section className="dri-exact-actions">
                <div className="dri-exact-actions-copy">
                  <p className="eyebrow">Tu colección</p>
                  <h2>¿Es esta tu versión?</h2>
                  <p>Guarda la edición física concreta que estás viendo.</p>
                </div>
                <LibraryActions printId={printDetail.id} />
              </section>

              <details className="dri-technical panel-soft">
                <summary>Datos técnicos</summary>
                <div className="dri-technical-grid">
                  <MetaLine label="Print ID" value={printDetail.id} />
                  <MetaLine label="Set de origen" value={printDetail.set_name} />
                  <MetaLine label="Código de origen" value={originCode} />
                  <MetaLine label="Variante" value={variantLabel || printDetail.variant} />
                  {cardmarket?.id_product ? <MetaLine label="Cardmarket idProduct" value={cardmarket.id_product} /> : null}
                </div>
              </details>

              <div className="dri-exact-navigation">
                <Link href={cardHref} className="dri-btn dri-btn-ghost">← Todas las versiones</Link>
                {physicalReleaseHref ? <Link href={physicalReleaseHref} className="dri-btn dri-btn-ghost">Ver edición</Link> : null}
                {(!physicalReleaseHref || releaseDiffersFromOrigin) && printDetail.set_code ? <Link href={originSetHref} className="dri-btn dri-btn-ghost">Ver set de origen</Link> : null}
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

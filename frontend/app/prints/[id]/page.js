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

const DEFAULT_DISPLAY_LOCALE = 'es-ES'

function MetaLine({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return <div className="dri-version-fact"><span>{label}</span><strong>{String(value)}</strong></div>
}

function IdentityChip({ children, accent = false }) {
  if (!children) return null
  return <span className={`v14-identity-chip ${accent ? 'is-accent' : ''}`}>{children}</span>
}

function money(value, currency = 'EUR', locale = DEFAULT_DISPLAY_LOCALE) {
  if (value === null || value === undefined) return '—'
  try {
    return new Intl.NumberFormat(locale || DEFAULT_DISPLAY_LOCALE, {
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

function PriceMetric({ label, value, currency, locale, featured = false }) {
  if (value === null || value === undefined) return null
  return (
    <div className={`ux-price-metric ${featured ? 'is-featured' : ''}`}>
      <span>{label}</span>
      <strong>{money(value, currency, locale)}</strong>
    </div>
  )
}

function PriceBlock({ price, cardmarket, locale }) {
  if (!price) {
    return (
      <section className="panel-soft identifiers ux-price-panel v14-market-panel">
        <div className="v14-market-head">
          <div>
            <p className="eyebrow">Mercado exacto</p>
            <h2>Cardmarket</h2>
          </div>
          <span className="v14-market-state is-empty">Sin precio actual</span>
        </div>
        <p className="detail-meta"><strong>Sin Price Guide actual.</strong> No reutilizamos el precio de otra edición: solo mostramos la contraparte exacta cuando existe en Cardmarket.</p>
        {cardmarket?.url ? (
          <a href={cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn v14-cardmarket-cta">
            Ver esta Print en Cardmarket ↗
          </a>
        ) : (
          <span className="v14-market-unlinked">Sin enlace Cardmarket exacto disponible</span>
        )}
      </section>
    )
  }

  const currency = price.currency || 'EUR'
  const hasConservative = price.conservative !== null && price.conservative !== undefined

  return (
    <section className="panel-soft identifiers ux-price-panel v14-market-panel">
      <div className="ux-price-heading v14-market-head">
        <div>
          <p className="eyebrow">Mercado exacto</p>
          <h2>Cardmarket</h2>
        </div>
        <div className="v14-market-primary">
          <span>{hasConservative ? 'Valor conservador' : 'Price Guide actual'}</span>
          {hasConservative ? <strong className="ux-price-main">{money(price.conservative, currency, locale)}</strong> : null}
        </div>
      </div>

      <div className="ux-price-grid v14-price-grid">
        <PriceMetric label="Mínimo" value={price.minimum} currency={currency} locale={locale} />
        <PriceMetric label="Conservador" value={price.conservative} currency={currency} locale={locale} featured />
        <PriceMetric label="Tendencia" value={price.trend} currency={currency} locale={locale} />
        <PriceMetric label="Media" value={price.average} currency={currency} locale={locale} />
      </div>

      <div className="v14-market-foot">
        <p className="detail-meta">{price.as_of ? `Actualizado ${new Date(price.as_of).toLocaleDateString(locale || DEFAULT_DISPLAY_LOCALE)}` : 'Precio de la versión comercial vinculada.'}</p>
        {cardmarket?.url ? (
          <a href={cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn v14-cardmarket-cta">
            Ver esta Print en Cardmarket ↗
          </a>
        ) : null}
      </div>

      <details className="dri-technical dri-price-method v14-price-method">
        <summary>Cómo se calcula</summary>
        <p className="detail-meta">El valor conservador usa Low Price EX+ para versiones no foil y Foil Low para versiones foil cuando Cardmarket publica esa métrica. No reutilizamos el precio de otra edición.</p>
      </details>
    </section>
  )
}

function resolveBrowserLocale() {
  if (typeof navigator === 'undefined') return DEFAULT_DISPLAY_LOCALE
  return navigator.languages?.[0] || navigator.language || DEFAULT_DISPLAY_LOCALE
}

export default function PrintDetailPage({ params }) {
  const { id } = use(params)
  const [printDetail, setPrintDetail] = useState(null)
  const [physicalReleases, setPhysicalReleases] = useState([])
  const [price, setPrice] = useState(null)
  const [cardmarket, setCardmarket] = useState(null)
  const [displayLocale, setDisplayLocale] = useState(DEFAULT_DISPLAY_LOCALE)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    async function loadPrint() {
      setLoading(true)
      setError('')
      const locale = resolveBrowserLocale()
      setDisplayLocale(locale)
      try {
        const [payload, releaseResponse, priceResponse] = await Promise.all([
          fetchPrintById(id, { locale }),
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
  const displayName = printDetail?.display?.name || printDetail?.display_name || printDetail?.card?.name || printDetail?.title || 'Nombre no disponible'
  const printedName = printDetail?.printed?.name || printDetail?.localized_card_name || null
  const showPrintedName = printedName && String(printedName).trim().toLocaleLowerCase() !== String(displayName).trim().toLocaleLowerCase()
  const displayEffect = printDetail?.display?.effect || printDetail?.display?.text || null
  const displayPendulumEffect = printDetail?.display?.pendulum_effect || null
  const displayFallback = Boolean(printDetail?.display?.fallback)
  const resolvedDisplayLanguage = printDetail?.display?.resolved_language?.toUpperCase?.() || null
  const requestedDisplayLanguage = printDetail?.display?.requested_language?.toUpperCase?.() || null
  const hasExactImage = printDetail?.image?.has_exact_image ?? Boolean(printDetail?.primary_image_url)

  return (
    <main>
      <TopNav />

      <section className="detail-shell v14-print-shell">
        {loading && <StatePanel title="Cargando impresión" description="Preparando la identidad física exacta y su mercado." />}
        {!loading && error && <StatePanel title="No pudimos cargar esta impresión" description={error} error />}

        {!loading && !error && printDetail && (
          <article className="detail-page v14-print-page">
            <div className="detail-media-column v14-media-column">
              <div className="detail-media detail-media-card dri-print-media v14-print-media">
                <FallbackImage
                  src={printDetail.primary_image_url}
                  alt={printedName || displayName}
                  className="detail-image"
                  placeholderClassName="catalog-placeholder image-fallback"
                  label={gameLabel}
                />
                <span className="v14-print-id-media">Print {printDetail.id}</span>
              </div>
              {!hasExactImage ? (
                <p className="detail-meta v14-image-warning">Imagen exacta pendiente de certificación. No mostramos la imagen de otra impresión para rellenar este hueco.</p>
              ) : null}
            </div>

            <div className="detail-content v14-print-content">
              <nav className="detail-breadcrumbs" aria-label="breadcrumb">
                <Link href={getGameExplorerHref(gameSlug)}>{gameLabel}</Link>
                <span>→</span>
                <Link href={cardHref}>{displayName}</Link>
                <span>→</span>
                <strong>Print {printDetail.id}</strong>
              </nav>

              <header className="dri-exact-head v14-exact-head">
                <div className="dri-exact-head-copy detail-title-block">
                  <div className="v14-title-kicker">
                    <span className="v14-exact-badge"><i /> Impresión exacta</span>
                    <span className="v14-print-id">Print ID {printDetail.id}</span>
                  </div>
                  <h1>{displayName}</h1>
                  {showPrintedName ? <p className="detail-meta">Nombre impreso: <strong>{printedName}</strong></p> : null}
                  <div className="v14-identity-chips" aria-label="Identidad física exacta">
                    <IdentityChip accent>{versionCode}</IdentityChip>
                    <IdentityChip>{printDetail.collector_number ? `#${printDetail.collector_number}` : null}</IdentityChip>
                    <IdentityChip>{printDetail.language?.toUpperCase()}</IdentityChip>
                    <IdentityChip>{printDetail.rarity}</IdentityChip>
                    <IdentityChip>{finishLabel}</IdentityChip>
                    <IdentityChip>{variantLabel}</IdentityChip>
                  </div>
                  <p className="v14-identity-rule">Idioma, acabado, variante y precio pertenecen a esta Print física concreta.</p>
                  {releaseDiffersFromOrigin ? (
                    <p className="detail-meta">Publicada físicamente en <strong>{physicalReleaseCode}</strong>; su carta/set de origen usa <strong>{originCode}</strong>.</p>
                  ) : null}
                </div>
              </header>

              <section className="dri-exact-actions v14-exact-actions">
                <div className="dri-exact-actions-copy">
                  <p className="eyebrow">Tu biblioteca</p>
                  <h2>Guardar esta Print exacta</h2>
                  <p>Las acciones se aplican a Print {printDetail.id}; nunca a otra edición de la misma carta.</p>
                </div>
                <LibraryActions printId={printDetail.id} />
              </section>

              <PriceBlock price={price} cardmarket={cardmarket} locale={displayLocale} />

              <section className="dri-version-summary panel-soft v14-version-summary">
                <div className="dri-version-summary-head">
                  <div>
                    <p className="eyebrow">Identidad física</p>
                    <h2>Información esencial</h2>
                  </div>
                  <span className="dri-language-pill">{printDetail.language?.toUpperCase() || '—'}</span>
                </div>
                <div className="dri-version-facts">
                  <MetaLine label="Edición física" value={primaryRelease?.name || printDetail.set_name} />
                  <MetaLine label="Código" value={versionCode} />
                  <MetaLine label="Número" value={printDetail.collector_number} />
                  <MetaLine label="Rareza" value={printDetail.rarity} />
                  <MetaLine label="Acabado" value={finishLabel} />
                  <MetaLine label="Idioma físico" value={printDetail.language?.toUpperCase()} />
                </div>
              </section>

              <section className="panel-soft identifiers v14-card-text">
                <div className="v14-section-heading">
                  <div>
                    <p className="eyebrow">Carta canónica</p>
                    <h2>Efecto / descripción</h2>
                  </div>
                  <Link href={cardHref}>Ver todas las impresiones →</Link>
                </div>
                {(displayEffect || displayPendulumEffect) ? (
                  <>
                    {displayEffect ? <p>{displayEffect}</p> : null}
                    {displayPendulumEffect ? (
                      <>
                        <h3>Efecto de Péndulo</h3>
                        <p>{displayPendulumEffect}</p>
                      </>
                    ) : null}
                    <p className="detail-meta">
                      {displayFallback
                        ? `No hay texto en ${requestedDisplayLanguage || 'el idioma solicitado'}; mostramos ${resolvedDisplayLanguage || 'el mejor texto disponible'}.`
                        : `Texto mostrado en ${resolvedDisplayLanguage || requestedDisplayLanguage || displayLocale}.`}
                      {printDetail?.display?.scope === 'card_display' ? ' Es una localización legible de la misma carta; la imagen sigue siendo la impresión física exacta.' : ''}
                    </p>
                  </>
                ) : (
                  <p className="detail-meta">Todavía no tenemos un texto localizado certificado para esta carta. La identidad física y la imagen exacta no se sustituyen por datos de otra impresión.</p>
                )}
              </section>

              <details className="dri-technical panel-soft v14-technical">
                <summary>Datos técnicos y procedencia</summary>
                <div className="dri-technical-grid">
                  <MetaLine label="Print ID" value={printDetail.id} />
                  <MetaLine label="Set de origen" value={printDetail.set_name} />
                  <MetaLine label="Código de origen" value={originCode} />
                  <MetaLine label="Variante" value={variantLabel || printDetail.variant} />
                  <MetaLine label="Locale de lectura" value={printDetail?.display?.requested_locale || displayLocale} />
                  {printDetail?.printed?.source ? <MetaLine label="Fuente texto impreso" value={printDetail.printed.source} /> : null}
                  {printDetail?.display?.source ? <MetaLine label="Fuente texto mostrado" value={printDetail.display.source} /> : null}
                  {cardmarket?.id_product ? <MetaLine label="Cardmarket idProduct" value={cardmarket.id_product} /> : null}
                </div>
              </details>

              <nav className="dri-exact-navigation v14-exact-navigation" aria-label="Navegación relacionada">
                <Link href={cardHref} className="dri-btn dri-btn-ghost">← Todas las impresiones</Link>
                {physicalReleaseHref ? <Link href={physicalReleaseHref} className="dri-btn dri-btn-ghost">Ver edición física</Link> : null}
                {(!physicalReleaseHref || releaseDiffersFromOrigin) && printDetail.set_code ? <Link href={originSetHref} className="dri-btn dri-btn-ghost">Ver set de origen</Link> : null}
              </nav>
            </div>
          </article>
        )}
      </section>
    </main>
  )
}

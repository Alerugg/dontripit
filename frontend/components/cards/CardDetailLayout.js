'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import LibraryActions from '../library/LibraryActions'
import { fetchCardPrintsPage } from '../../lib/catalog/client'
import { getGameExplorerHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../lib/catalog/games'

const PRINTS_PAGE_SIZE = 24

function DetailStat({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return (
    <div className="detail-stat panel-soft" style={{ display: 'block' }}>
      <span>{label}</span>
      <strong>{String(value)}</strong>
    </div>
  )
}

function money(value, currency = 'EUR') {
  if (value === null || value === undefined) return null
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

function primaryPhysicalRelease(print) {
  return Array.isArray(print?.physical_releases) && print.physical_releases.length
    ? print.physical_releases[0]
    : null
}

function physicalReleaseName(print) {
  const explicit = Array.isArray(print?.physical_release_names) ? print.physical_release_names[0] : null
  return explicit || primaryPhysicalRelease(print)?.name || null
}

function physicalReleaseCode(print) {
  const release = primaryPhysicalRelease(print)
  if (release?.code) return String(release.code).toUpperCase()
  const name = physicalReleaseName(print)
  const bracket = String(name || '').match(/\[([^\]]+)\]/)
  return bracket?.[1] ? bracket[1].toUpperCase() : null
}

function selectedMeta(print) {
  const releaseCode = physicalReleaseCode(print)
  const originCode = print?.set_code ? String(print.set_code).toUpperCase() : null
  const distinctOrigin = releaseCode && originCode && releaseCode.replace(/[^A-Z0-9]/g, '') !== originCode.replace(/[^A-Z0-9]/g, '')

  return [
    releaseCode ? `Lanzamiento ${releaseCode}` : originCode,
    distinctOrigin ? `Carta/origen ${originCode}` : null,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.language?.toUpperCase(),
    print?.rarity,
    print?.is_foil ? 'Foil' : null,
    print?.variant && print.variant !== 'default' ? print.variant : null,
  ].filter(Boolean)
}

function optionLabel(print) {
  const releaseCode = physicalReleaseCode(print)
  const originCode = print?.set_code ? String(print.set_code).toUpperCase() : null
  const distinctOrigin = releaseCode && originCode && releaseCode.replace(/[^A-Z0-9]/g, '') !== originCode.replace(/[^A-Z0-9]/g, '')
  return [
    releaseCode || originCode || print?.set_name,
    distinctOrigin ? `origen ${originCode}` : null,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.variant && print.variant !== 'default' ? print.variant : null,
    print?.is_foil ? 'foil' : null,
  ].filter(Boolean).join(' · ')
}

export default function CardDetailLayout({ card, routeGameSlug = '' }) {
  const gameSlug = normalizeGameSlug(routeGameSlug || card?.game_slug || card?.game || '')
  const gameConfig = getGameConfig(gameSlug)
  const gameLabel = gameConfig?.name || card?.game || 'TCG'
  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])
  const initialPrints = useMemo(() => (Array.isArray(card?.prints) ? card.prints : []), [card])
  const setCount = Array.isArray(card?.sets) ? card.sets.length : 0
  const [printPage, setPrintPage] = useState(1)
  const [printsPayload, setPrintsPayload] = useState(null)
  const [printsLoading, setPrintsLoading] = useState(false)
  const [printsError, setPrintsError] = useState('')
  const [selectedPrintId, setSelectedPrintId] = useState(initialPrints[0]?.id || null)
  const [price, setPrice] = useState(null)
  const [cardmarket, setCardmarket] = useState(null)
  const [priceLoading, setPriceLoading] = useState(false)

  useEffect(() => {
    setPrintPage(1)
    setPrintsPayload(null)
    setPrintsError('')
  }, [card?.id])

  useEffect(() => {
    if (!card?.id) return undefined
    let cancelled = false
    setPrintsLoading(true)
    setPrintsError('')
    fetchCardPrintsPage(card.id, {
      limit: PRINTS_PAGE_SIZE,
      offset: (printPage - 1) * PRINTS_PAGE_SIZE,
    })
      .then((payload) => {
        if (!cancelled) setPrintsPayload(payload)
      })
      .catch((error) => {
        if (!cancelled) setPrintsError(error?.message || 'No pudimos cargar esta página de versiones.')
      })
      .finally(() => { if (!cancelled) setPrintsLoading(false) })
    return () => { cancelled = true }
  }, [card?.id, printPage])

  const prints = useMemo(
    () => (Array.isArray(printsPayload?.items) ? printsPayload.items : initialPrints),
    [initialPrints, printsPayload],
  )
  const variantCount = Number(printsPayload?.total ?? card?.prints_pagination?.total ?? initialPrints.length)
  const totalPrintPages = Math.max(1, Math.ceil(variantCount / PRINTS_PAGE_SIZE))

  useEffect(() => {
    if (!prints.length) {
      setSelectedPrintId(null)
      return
    }
    if (!prints.some((print) => String(print.id) === String(selectedPrintId))) {
      setSelectedPrintId(prints[0].id)
    }
  }, [prints, selectedPrintId])

  const selectedPrint = useMemo(
    () => prints.find((print) => String(print.id) === String(selectedPrintId)) || prints[0] || null,
    [prints, selectedPrintId],
  )

  useEffect(() => {
    if (!selectedPrint?.id) {
      setPrice(null)
      setCardmarket(null)
      return undefined
    }

    let cancelled = false
    setPriceLoading(true)
    fetch(`/api/prices/print/${selectedPrint.id}`, { cache: 'no-store' })
      .then(async (response) => response.ok ? response.json() : { price: null, cardmarket: null })
      .then((payload) => {
        if (!cancelled) {
          setPrice(payload?.price || null)
          setCardmarket(payload?.cardmarket || payload?.price?.cardmarket || null)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setPrice(null)
          setCardmarket(null)
        }
      })
      .finally(() => { if (!cancelled) setPriceLoading(false) })

    return () => { cancelled = true }
  }, [selectedPrint?.id])

  const conservative = money(price?.conservative, price?.currency || 'EUR')
  const observed = money(price?.value, price?.currency || 'EUR')
  const selectedReleaseName = physicalReleaseName(selectedPrint)
  const selectedReleaseCode = physicalReleaseCode(selectedPrint)
  const pageStart = variantCount ? ((printPage - 1) * PRINTS_PAGE_SIZE) + 1 : 0
  const pageEnd = Math.min(printPage * PRINTS_PAGE_SIZE, variantCount)

  return (
    <article className="detail-page">
      <div className="detail-media-column">
        <div className="detail-media detail-media-card">
          <FallbackImage
            src={selectedPrint?.primary_image_url || card.primary_image_url}
            alt={card.name}
            className="detail-image"
            placeholderClassName="catalog-placeholder image-fallback"
            label={card.game || 'Carta'}
          />
        </div>

        <div className="panel-soft detail-summary-stack">
          <p className="eyebrow">Carta</p>
          <strong>{card.name}</strong>
          <span>{gameLabel}</span>
        </div>
      </div>

      <div className="detail-content">
        <nav className="detail-breadcrumbs" aria-label="breadcrumb">
          <Link href={getGameExplorerHref(gameSlug)}>{gameLabel}</Link>
          <span>→</span>
          {primarySet?.code ? (
            <Link href={getSetHref(gameSlug, primarySet.code)}>{primarySet.name || primarySet.code}</Link>
          ) : (
            <span>{primarySet?.name || 'Cartas'}</span>
          )}
          <span>→</span>
          <strong>{card.name}</strong>
        </nav>

        <div className="detail-title-block">
          <p className="eyebrow">Carta encontrada</p>
          <h1>{card.name}</h1>
          <p className="detail-intro">Explora todas sus impresiones físicas sin cortes. Cada versión conserva su propio Print ID, lanzamiento, imagen, colección, wishlist y enlace Cardmarket cuando existe una correspondencia exacta.</p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Juego" value={gameLabel} />
          <DetailStat label="Versiones físicas" value={variantCount} />
          <DetailStat label="Sets relacionados" value={setCount} />
        </section>

        {printsError ? <p className="detail-meta">{printsError}</p> : null}

        {selectedPrint ? (
          <section className="dri-card-workspace">
            <div className="dri-card-workspace-head">
              <div>
                <p className="eyebrow">Versión seleccionada</p>
                <h2>{selectedReleaseName || selectedPrint.set_name || selectedPrint.set_code || 'Edición física'}</h2>
                <p>{selectedReleaseName ? 'Este es el lanzamiento físico certificado de esta impresión. El set de origen de la carta puede ser distinto.' : 'Esta impresión se identifica por su set, número, idioma, acabado y variante exactos.'}</p>
              </div>
              <span className="dri-card-workspace-count">{variantCount} {variantCount === 1 ? 'versión' : 'versiones'}</span>
            </div>

            <div className="dri-selected-print">
              <div className="dri-selected-print-media">
                <FallbackImage
                  src={selectedPrint.primary_image_url || card.primary_image_url}
                  alt={`${card.name} · ${selectedReleaseCode || selectedPrint.set_code || selectedPrint.set_name || 'versión'}`}
                  className="detail-image"
                  placeholderClassName="image-fallback"
                  label={selectedReleaseCode || selectedPrint.set_code || gameLabel}
                />
              </div>
              <div className="dri-selected-print-copy">
                <h3>{card.name}</h3>
                {selectedReleaseName ? <small className="detail-meta"><strong>Lanzamiento físico:</strong> {selectedReleaseName}</small> : null}
                {selectedPrint.set_name ? <small className="detail-meta"><strong>Set/carta de origen:</strong> {selectedPrint.set_name}</small> : null}
                <div className="dri-selected-print-meta">
                  {selectedMeta(selectedPrint).map((value) => <span key={value}>{value}</span>)}
                </div>

                {priceLoading ? (
                  <div className="dri-inline-price is-empty">Consultando precio Cardmarket…</div>
                ) : price ? (
                  <div className="dri-inline-price">
                    <div className="dri-inline-price-main">
                      <strong>{conservative || observed || 'Precio disponible'}</strong>
                      <span>{conservative ? 'valor conservador' : 'precio observado'}</span>
                    </div>
                    <small>{price.source || 'Cardmarket'}{price.as_of ? ` · ${new Date(price.as_of).toLocaleDateString('es-ES')}` : ''}</small>
                  </div>
                ) : (
                  <div className="dri-inline-price is-empty">Sin Price Guide actual para esta versión exacta. No reutilizamos el precio de otra impresión.</div>
                )}

                <div className="dri-selected-print-actions">
                  <LibraryActions printId={selectedPrint.id} />
                  {cardmarket?.url ? (
                    <a
                      href={cardmarket.url}
                      target="_blank"
                      rel="noopener noreferrer sponsored"
                      className="dri-btn"
                    >
                      Comprar esta versión en Cardmarket ↗
                    </a>
                  ) : null}
                  <Link href={getPrintHref(selectedPrint.id)} className="dri-btn dri-btn-ghost">Abrir ficha exacta #{selectedPrint.id} →</Link>
                </div>
              </div>
            </div>

            {prints.length ? (
              <>
                <div className="dri-variant-strip" aria-label="Versiones físicas de la carta">
                  {prints.map((print) => {
                    const active = String(print.id) === String(selectedPrint.id)
                    return (
                      <button
                        key={print.id}
                        type="button"
                        className={`dri-variant-option ${active ? 'is-selected' : ''}`}
                        aria-pressed={active}
                        onClick={() => setSelectedPrintId(print.id)}
                      >
                        <div className="dri-variant-option-thumb">
                          <FallbackImage
                            src={print.primary_image_url}
                            alt={`${card.name} · ${optionLabel(print)}`}
                            className="detail-image"
                            placeholderClassName="image-fallback"
                            label={physicalReleaseCode(print) || print.set_code || 'Versión'}
                          />
                        </div>
                        <strong>{physicalReleaseCode(print) || String(print.set_code || '').toUpperCase() || 'Versión'}</strong>
                        <small>{optionLabel(print) || 'Edición física identificada'}</small>
                      </button>
                    )
                  })}
                </div>

                <div className="toolbar-row" aria-label="Paginación de versiones físicas">
                  <button type="button" className="dri-btn dri-btn-ghost" disabled={printPage <= 1 || printsLoading} onClick={() => setPrintPage((current) => Math.max(1, current - 1))}>← Versiones anteriores</button>
                  <span className="detail-meta">{printsLoading ? 'Cargando…' : `${pageStart}-${pageEnd} de ${variantCount} · página ${printPage} de ${totalPrintPages}`}</span>
                  <button type="button" className="dri-btn dri-btn-ghost" disabled={printPage >= totalPrintPages || printsLoading} onClick={() => setPrintPage((current) => Math.min(totalPrintPages, current + 1))}>Más versiones →</button>
                </div>
              </>
            ) : null}
          </section>
        ) : null}

        {(card.sets || []).length ? (
          <section className="detail-section-block panel-soft">
            <div className="section-heading compact">
              <p className="eyebrow">Aparece en</p>
              <h2>Todos los sets relacionados</h2>
            </div>
            <div className="chip-row">
              {(card.sets || []).map((setItem) => (
                <Link
                  key={setItem.id || setItem.code}
                  className="filter-chip active"
                  href={setItem.code ? getSetHref(gameSlug, setItem.code) : getGameExplorerHref(gameSlug)}
                >
                  {setItem.code ? `${String(setItem.code).toUpperCase()} · ` : ''}
                  {setItem.name || 'Set'}
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        {!variantCount && !printsLoading ? (
          <section className="detail-section-block panel-soft">
            <p className="eyebrow">Sin versiones</p>
            <h2>Todavía no tenemos una edición física asociada.</h2>
            <p className="detail-meta">La carta existe en el catálogo, pero no vamos a inventar una versión para permitir acciones de colección.</p>
          </section>
        ) : null}
      </div>
    </article>
  )
}

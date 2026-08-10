'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import LibraryActions from '../library/LibraryActions'
import { getGameExplorerHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../lib/catalog/games'

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

function selectedMeta(print) {
  return [
    print?.set_code,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.language?.toUpperCase(),
    print?.rarity,
    print?.finish && print.finish !== 'default' ? print.finish : null,
    print?.variant && print.variant !== 'default' ? print.variant : null,
  ].filter(Boolean)
}

function optionLabel(print) {
  return [
    print?.set_code || print?.set_name,
    print?.collector_number ? `#${print.collector_number}` : null,
    print?.variant && print.variant !== 'default' ? print.variant : null,
    print?.finish && print.finish !== 'default' ? print.finish : null,
  ].filter(Boolean).join(' · ')
}

export default function CardDetailLayout({ card, routeGameSlug = '' }) {
  const gameSlug = normalizeGameSlug(routeGameSlug || card?.game_slug || card?.game || '')
  const gameConfig = getGameConfig(gameSlug)
  const gameLabel = gameConfig?.name || card?.game || 'TCG'
  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])
  const prints = useMemo(() => (Array.isArray(card?.prints) ? card.prints : []), [card])
  const variantCount = prints.length
  const setCount = Array.isArray(card?.sets) ? card.sets.length : 0
  const [selectedPrintId, setSelectedPrintId] = useState(prints[0]?.id || null)
  const [price, setPrice] = useState(null)
  const [cardmarket, setCardmarket] = useState(null)
  const [priceLoading, setPriceLoading] = useState(false)

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
          <p className="detail-intro">Elige abajo la edición física que tienes o buscas. Precio, colección y wishlist se actualizan aquí mismo: no necesitas abrir otra ficha para actuar.</p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Juego" value={gameLabel} />
          <DetailStat label="Versiones" value={variantCount} />
          <DetailStat label="Sets relacionados" value={setCount} />
        </section>

        {selectedPrint ? (
          <section className="dri-card-workspace">
            <div className="dri-card-workspace-head">
              <div>
                <p className="eyebrow">Versión seleccionada</p>
                <h2>{selectedPrint.set_name || selectedPrint.set_code || 'Edición física'}</h2>
                <p>Cambia de versión más abajo. Tus acciones siempre se aplican a la edición que está seleccionada.</p>
              </div>
              <span className="dri-card-workspace-count">{variantCount} {variantCount === 1 ? 'versión' : 'versiones'}</span>
            </div>

            <div className="dri-selected-print">
              <div className="dri-selected-print-media">
                <FallbackImage
                  src={selectedPrint.primary_image_url || card.primary_image_url}
                  alt={`${card.name} · ${selectedPrint.set_code || selectedPrint.set_name || 'versión'}`}
                  className="detail-image"
                  placeholderClassName="image-fallback"
                  label={selectedPrint.set_code || gameLabel}
                />
              </div>
              <div className="dri-selected-print-copy">
                <h3>{card.name}</h3>
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
                  <div className="dri-inline-price is-empty">Sin Price Guide actual para esta versión exacta. Si existe una correspondencia segura, puedes abrir Cardmarket para comprobar sus ofertas.</div>
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
                      Comprar en Cardmarket ↗
                    </a>
                  ) : null}
                  <Link href={getPrintHref(selectedPrint.id)} className="dri-btn dri-btn-ghost">Ver todos los detalles →</Link>
                </div>
              </div>
            </div>

            {prints.length > 1 ? (
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
                          alt={optionLabel(print) || card.name}
                          className="detail-image"
                          placeholderClassName="image-fallback"
                          label={print.set_code || 'Versión'}
                        />
                      </div>
                      <strong>{print.set_code || print.set_name || 'Versión'}</strong>
                      <small>{optionLabel(print) || 'Edición disponible'}</small>
                    </button>
                  )
                })}
              </div>
            ) : null}
          </section>
        ) : null}

        {(card.sets || []).length ? (
          <section className="detail-section-block panel-soft">
            <div className="section-heading compact">
              <p className="eyebrow">Aparece en</p>
              <h2>Sets relacionados</h2>
            </div>
            <div className="chip-row">
              {(card.sets || []).map((setItem) => (
                <Link
                  key={setItem.id || setItem.code}
                  className="filter-chip active"
                  href={setItem.code ? getSetHref(gameSlug, setItem.code) : getGameExplorerHref(gameSlug)}
                >
                  {setItem.code ? `${setItem.code} · ` : ''}
                  {setItem.name || 'Set'}
                </Link>
              ))}
            </div>
          </section>
        ) : null}

        {!variantCount ? (
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

'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import CardVersionBrowser from './CardVersionBrowser'
import { getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'
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

export default function CardDetailLayout({ card, routeGameSlug = '' }) {
  const gameSlug = normalizeGameSlug(routeGameSlug || card?.game_slug || card?.game || '')
  const gameConfig = getGameConfig(gameSlug)
  const gameLabel = gameConfig?.name || card?.game || 'TCG'
  const primarySet = Array.isArray(card?.sets) ? card.sets[0] || null : null
  const setCount = Array.isArray(card?.sets) ? card.sets.length : 0
  const printCount = Number(card?.prints_pagination?.total ?? card?.prints?.length ?? 0)

  return (
    <article className="detail-page">
      <div className="detail-media-column">
        <div className="detail-media detail-media-card">
          <FallbackImage
            src={card.primary_image_url}
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
          <small className="detail-meta">Imagen de referencia de la carta. El idioma físico se elige dentro de cada versión.</small>
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
          <p className="detail-intro">
            Primero elige la versión de la carta. Después verás en qué idiomas físicos existe y podrás abrir la impresión exacta o ir directamente a esa versión en Cardmarket.
          </p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Juego" value={gameLabel} />
          <DetailStat label="Impresiones físicas conocidas" value={printCount} />
          <DetailStat label="Sets relacionados" value={setCount} />
        </section>

        <CardVersionBrowser cardId={card.id} cardName={card.name} gameLabel={gameLabel} />

        {(card.sets || []).length ? (
          <section className="detail-section-block panel-soft">
            <div className="section-heading compact">
              <p className="eyebrow">Sets relacionados</p>
              <h2>Dónde aparece esta carta</h2>
              <p className="detail-meta">Esta lista es secundaria. Para encontrar una versión concreta usa el selector de versiones de arriba.</p>
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
      </div>
    </article>
  )
}

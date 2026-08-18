'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import CardVersionBrowser from './CardVersionBrowser'
import { getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../lib/catalog/games'

export default function CardDetailLayout({ card, routeGameSlug = '' }) {
  const gameSlug = normalizeGameSlug(routeGameSlug || card?.game_slug || card?.game || '')
  const gameConfig = getGameConfig(gameSlug)
  const gameLabel = gameConfig?.name || card?.game || 'TCG'

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
      </div>

      <div className="detail-content">
        <nav className="detail-breadcrumbs" aria-label="breadcrumb">
          <Link href={getGameExplorerHref(gameSlug)}>{gameLabel}</Link>
          <span>→</span>
          <strong>{card.name}</strong>
        </nav>

        <div className="detail-title-block">
          <p className="eyebrow">{gameLabel}</p>
          <h1>{card.name}</h1>
          <p className="detail-intro">Encuentra la edición correcta, elige su idioma físico y abre esa versión exacta o su ficha de Cardmarket.</p>
        </div>

        <CardVersionBrowser cardId={card.id} cardName={card.name} gameLabel={gameLabel} />

        {(card.sets || []).length ? (
          <details className="detail-section-block panel-soft">
            <summary>Sets relacionados ({card.sets.length})</summary>
            <div className="chip-row" style={{ marginTop: 14 }}>
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
          </details>
        ) : null}
      </div>
    </article>
  )
}

'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import CardVersionBrowser from './CardVersionBrowser'
import { getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'
import { getGameConfig, normalizeGameSlug } from '../../lib/catalog/games'

function safeCount(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) && number >= 0 ? number : fallback
}

export default function CardDetailLayout({ card, routeGameSlug = '' }) {
  const gameSlug = normalizeGameSlug(routeGameSlug || card?.game_slug || card?.game || '')
  const gameConfig = getGameConfig(gameSlug)
  const gameLabel = gameConfig?.name || card?.game || 'TCG'
  const printCount = safeCount(card?.prints_pagination?.total, (card?.prints || []).length)
  const setCount = (card?.sets || []).length

  return (
    <article className="detail-page v9-card-detail">
      <div className="detail-media-column v9-card-media-column">
        <div className="detail-media detail-media-card v9-card-media">
          <FallbackImage
            src={card.primary_image_url}
            alt={card.name}
            className="detail-image"
            placeholderClassName="catalog-placeholder image-fallback"
            label={card.game || 'Carta'}
          />
        </div>
        <p className="v9-representative-note">Imagen representativa · la edición física se elige a la derecha.</p>
      </div>

      <div className="detail-content v9-card-content">
        <nav className="detail-breadcrumbs v9-card-breadcrumbs" aria-label="breadcrumb">
          <Link href={getGameExplorerHref(gameSlug)}>{gameLabel}</Link>
          <span>→</span>
          <strong>{card.name}</strong>
        </nav>

        <header className="detail-title-block v9-card-header">
          <div className="v9-card-kicker-row">
            <span className="v9-identity-pill">Carta canónica</span>
            <span className="v9-game-label">{gameLabel}</span>
          </div>
          <h1>{card.name}</h1>
          <p className="detail-intro v9-card-intro">
            Esta es la identidad lógica de la carta. Elige debajo la <strong>impresión física exacta</strong> para fijar set, idioma, acabado, variante y mercado.
          </p>
        </header>

        <section className="v9-card-truth" aria-label="Cobertura de la carta">
          <div className="v9-card-stat">
            <span>Impresiones físicas</span>
            <strong>{printCount.toLocaleString('es-ES')}</strong>
          </div>
          <div className="v9-card-stat">
            <span>Sets relacionados</span>
            <strong>{setCount.toLocaleString('es-ES')}</strong>
          </div>
          <div className="v9-card-market-rule">
            <span>Mercado</span>
            <strong>Solo en la impresión exacta</strong>
            <small>No agregamos precios de distintas ediciones, idiomas o acabados.</small>
          </div>
        </section>

        <CardVersionBrowser cardId={card.id} cardName={card.name} gameLabel={gameLabel} />

        {(card.sets || []).length ? (
          <section className="v9-related-sets" aria-labelledby="v9-related-sets-title">
            <div className="v9-related-sets-head">
              <div>
                <p className="eyebrow">Presencia en catálogo</p>
                <h2 id="v9-related-sets-title">Sets relacionados</h2>
              </div>
              <span>{setCount} set{setCount === 1 ? '' : 's'}</span>
            </div>
            <div className="v9-related-set-grid">
              {(card.sets || []).map((setItem) => (
                <Link
                  key={setItem.id || setItem.code}
                  className="v9-related-set"
                  href={setItem.code ? getSetHref(gameSlug, setItem.code) : getGameExplorerHref(gameSlug)}
                >
                  <strong>{setItem.code ? String(setItem.code).toUpperCase() : 'Set'}</strong>
                  <span>{setItem.name || 'Set del catálogo'}</span>
                  <i aria-hidden="true">→</i>
                </Link>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </article>
  )
}

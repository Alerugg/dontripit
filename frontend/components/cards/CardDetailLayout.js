'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import VariantPicker from '../catalog/VariantPicker'
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
  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])
  const prints = useMemo(() => (Array.isArray(card?.prints) ? card.prints : []), [card])
  const variantCount = prints.length
  const setCount = Array.isArray(card?.sets) ? card.sets.length : 0

  return (
    <article className="detail-page panel">
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
          <p className="detail-intro">Ahora elige la versión física que te interesa. Cada set, idioma o variante se guarda por separado en tu colección o wishlist.</p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Juego" value={gameLabel} />
          <DetailStat label="Versiones disponibles" value={variantCount} />
          <DetailStat label="Sets relacionados" value={setCount} />
        </section>

        {(card.sets || []).length ? (
          <section className="detail-section-block panel-soft">
            <div className="section-heading compact">
              <p className="eyebrow">Aparece en</p>
              <h2>Sets</h2>
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

        <section className="detail-section-block">
          <div className="section-heading compact">
            <p className="eyebrow">Elige una versión</p>
            <h2>¿Cuál tienes o cuál buscas?</h2>
            <p>{variantCount ? `${variantCount} ${variantCount === 1 ? 'versión disponible' : 'versiones disponibles'}.` : 'Todavía no tenemos versiones registradas para esta carta.'}</p>
          </div>

          {variantCount ? (
            <div className="ux-detail-guide">
              <span aria-hidden="true">→</span>
              <div><strong>Siguiente paso:</strong> compara set, idioma y variante, abre la correcta y desde ahí añádela a tu colección o wishlist.</div>
            </div>
          ) : null}

          <VariantPicker prints={prints} gameSlug={gameSlug} />
        </section>
      </div>
    </article>
  )
}

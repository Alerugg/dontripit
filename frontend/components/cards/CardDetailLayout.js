'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import VariantPicker from '../catalog/VariantPicker'
import { getCardHref, getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'

function DetailStat({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return (
    <div className="detail-stat panel-soft">
      <span>{label}</span>
      <strong>{String(value)}</strong>
    </div>
  )
}

export default function CardDetailLayout({ card, searchState }) {
  const gameSlug = card?.game || ''
  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])

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
          <p>{primarySet?.name || 'Set principal pendiente de enriquecer'}</p>
        </div>
      </div>

      <div className="detail-content">
        <nav className="detail-breadcrumbs" aria-label="breadcrumb">
          <Link href={getGameExplorerHref(gameSlug)}>{card.game || 'TCG'}</Link>
          <span>→</span>
          {primarySet?.code ? (
            <Link href={getSetHref(gameSlug, primarySet.code)}>{primarySet.name || primarySet.code}</Link>
          ) : (
            <span>{primarySet?.name || 'Colección'}</span>
          )}
          <span>→</span>
          <strong>{card.name}</strong>
        </nav>

        <div className="detail-title-block">
          <p className="eyebrow">Carta</p>
          <h1>{card.name}</h1>
          <p className="detail-intro">
            Ficha maestra de la carta con variantes, sets y metadatos legibles para navegar sin ambigüedad.
          </p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Card ID" value={card.id} />
          <DetailStat label="Juego" value={card.game} />
          <DetailStat label="Idioma base" value={card.language} />
          <DetailStat label="Variantes" value={card.prints?.length || 0} />
        </section>

        <section className="detail-section-block panel-soft">
          <div className="section-heading compact">
            <p className="eyebrow">Colecciones</p>
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

        <section className="detail-section-block panel-soft">
          <div className="section-heading compact">
            <p className="eyebrow">Datos clave</p>
            <h2>Metadatos legibles</h2>
          </div>
          <div className="meta-grid meta-grid-columns">
            <p><strong>Oracle ID:</strong> {card.external_ids?.oracle_id || '—'}</p>
            <p><strong>Konami ID:</strong> {card.external_ids?.konami_id || '—'}</p>
            <p><strong>TCGPlayer:</strong> {card.external_ids?.tcgplayer_id || '—'}</p>
            <p><strong>Ruta canónica:</strong> <Link href={getCardHref(gameSlug, card.id, searchState)}>Abrir esta carta</Link></p>
          </div>
        </section>

        <section className="detail-section-block">
          <div className="section-heading compact">
            <p className="eyebrow">Variantes</p>
            <h2>Ediciones, finishes e idiomas</h2>
            <p>Las variantes viven dentro de la carta para evitar duplicados en búsqueda y mantener contexto completo.</p>
          </div>
          <VariantPicker prints={card.prints || []} gameSlug={gameSlug} />
        </section>
      </div>
    </article>
  )
}

'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import VariantPicker from '../catalog/VariantPicker'
import { getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'

function DetailStat({ label, value }) {
  if (!value && value !== false && value !== 0) return null
  return (
    <div className="detail-stat panel-soft">
      <span>{label}</span>
      <strong>{String(value)}</strong>
    </div>
  )
}

export default function CardDetailLayout({ card }) {
  const gameSlug = card?.game || ''
  const primarySet = useMemo(() => card?.sets?.[0] || null, [card])
  const externalIds = [
    ['Oracle ID', card.external_ids?.oracle_id],
    ['Konami ID', card.external_ids?.konami_id],
    ['TCGPlayer', card.external_ids?.tcgplayer_id],
  ].filter(([, value]) => value)

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
          <p>{primarySet?.name || card.game || 'TCG'}</p>
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
          {card.text && <p className="detail-intro">{card.text}</p>}
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
            <h2>Colecciones</h2>
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

        {externalIds.length > 0 && (
          <section className="detail-section-block panel-soft">
            <div className="section-heading compact">
              <p className="eyebrow">Datos clave</p>
              <h2>Datos clave</h2>
            </div>
            <div className="meta-grid meta-grid-columns">
              {externalIds.map(([label, value]) => (
                <p key={label}><strong>{label}:</strong> {value}</p>
              ))}
            </div>
          </section>
        )}

        <section className="detail-section-block">
          <div className="section-heading compact">
            <p className="eyebrow">Variantes</p>
            <h2>Variantes</h2>
          </div>
          <VariantPicker prints={card.prints || []} gameSlug={gameSlug} />
        </section>
      </div>
    </article>
  )
}

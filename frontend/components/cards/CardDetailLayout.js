'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import VariantPicker from '../catalog/VariantPicker'
import { getCardHref, getGameExplorerHref, getSetHref } from '../../lib/catalog/routes'

function DetailStat({ label, value }) {
  if (!value && value !== false) return null
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

  return (
    <article className="panel detail-page detail-card-page-v2">
      <div className="detail-media-column">
        <div className="detail-media detail-media-card">
          <FallbackImage
            src={card.primary_image_url}
            alt={card.name}
            className="detail-image"
            placeholderClassName="catalog-placeholder image-fallback"
            label={card.game || 'TCG'}
          />
        </div>

        <div className="panel-soft detail-summary-stack">
          <p className="kicker">Juego</p>
          <strong>{card.game || 'TCG'}</strong>
          <p>{primarySet?.name || 'Set principal pendiente de enriquecer'}</p>
        </div>
      </div>

      <div className="detail-content">
        <nav className="detail-breadcrumbs" aria-label="breadcrumb">
          <Link href={gameSlug ? getGameExplorerHref(gameSlug) : '/explorer'}>{card.game || 'TCG'}</Link>
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
          <p className="kicker">Carta</p>
          <h1>{card.name}</h1>
          <p className="detail-intro">
            Vista reorganizada para entender rápido juego, set, variantes y datos importantes sin lenguaje ambiguo.
          </p>
        </div>

        <section className="detail-stats-grid">
          <DetailStat label="Card ID" value={card.id} />
          <DetailStat label="Juego" value={card.game} />
          <DetailStat label="Idioma base" value={card.language} />
          <DetailStat label="Variantes" value={card.prints?.length} />
        </section>

        <section className="detail-section-block panel-soft">
          <div className="section-head">
            <div>
              <h2>Sets / colecciones</h2>
              <p>Enlaces base para navegar la jerarquía del catálogo por expansión.</p>
            </div>
          </div>
          <div className="chip-list">
            {(card.sets || []).map((setItem) => (
              <Link
                key={setItem.id || setItem.code}
                className="chip"
                href={setItem.code ? getSetHref(gameSlug, setItem.code) : getGameExplorerHref(gameSlug)}
              >
                {setItem.code ? `${setItem.code} · ` : ''}
                {setItem.name || 'Set'}
              </Link>
            ))}
          </div>
        </section>

        <section className="detail-section-block panel-soft">
          <div className="section-head">
            <div>
              <h2>Datos clave</h2>
              <p>Bloque compacto para IDs y metadatos que sí ayudan a identificar la carta.</p>
            </div>
          </div>
          <div className="meta-grid meta-grid-columns">
            <p><strong>Oracle ID:</strong> {card.external_ids?.oracle_id || '—'}</p>
            <p><strong>Konami ID:</strong> {card.external_ids?.konami_id || '—'}</p>
            <p><strong>TCGPlayer:</strong> {card.external_ids?.tcgplayer_id || '—'}</p>
            <p><strong>Ruta rápida:</strong> <Link href={getCardHref(gameSlug, card.id)}>Abrir detalle canónico</Link></p>
          </div>
        </section>

        <section className="detail-section-block">
          <div className="section-head">
            <div>
              <h2>Variantes / prints</h2>
              <p>Cada variante incluye miniatura para distinguir rápido arte, idioma, rareza y collector number.</p>
            </div>
          </div>
          <VariantPicker prints={card.prints || []} gameSlug={gameSlug} />
        </section>
      </div>
    </article>
  )
}

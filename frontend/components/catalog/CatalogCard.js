import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { getCardHref, getSetHref } from '../../lib/catalog/routes'

function buildSubtitle(item) {
  return [
    item.set_name || item.summary_label,
    item.language,
    item.variant_count ? `${item.variant_count} variante${item.variant_count === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' · ')
}

function buildMetaChips(item) {
  return [
    item.set_code,
    item.rarity,
    item.year,
  ].filter(Boolean)
}

export default function CatalogCard({ item, view = 'grid', queryState, debugImage = false }) {
  const title = item.name || item.title || 'Carta sin título'
  const href = item.type === 'set'
    ? getSetHref(item.game, item.set_code || item.code)
    : getCardHref(item.game, item.card_id || item.id, queryState)

  return (
    <Link href={href} className={`catalog-card ${view === 'list' ? 'list' : ''}`}>
      <div className="catalog-image-wrap">
        <FallbackImage
          src={item.primary_image_url}
          alt={title}
          className="catalog-image"
          placeholderClassName="catalog-placeholder image-fallback"
          label={item.game || 'Carta'}
          debug={debugImage}
          debugLabel={item.game === 'onepiece' ? 'One Piece probe' : item.game === 'pokemon' ? 'Pokémon probe' : ''}
        />
      </div>

      <div className="catalog-card-content">
        <div className="catalog-card-head">
          <div>
            <p className="meta-game">{item.game || 'TCG'}</p>
            <h3>{title}</h3>
          </div>
          <span className="badge badge-card">{item.type === 'set' ? 'Set' : 'Carta'}</span>
        </div>

        <p className="meta-subtitle">{buildSubtitle(item) || 'Carta'}</p>

        <div className="catalog-card-footer">
          <div className="catalog-meta-row">
            {buildMetaChips(item).map((meta) => (
              <span key={meta} className="catalog-meta-chip">{meta}</span>
            ))}
          </div>
          {item.variant_count ? <span className="catalog-variant-pill">{item.variant_count} variantes</span> : null}
        </div>
      </div>
    </Link>
  )
}

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

export default function CatalogCard({ item, view = 'grid', queryState }) {
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
        />
      </div>

      <div className="catalog-card-content">
        <div className="catalog-card-head">
          <div>
            <p className="meta-game">{item.game || 'TCG'}</p>
            <h3>{title}</h3>
          </div>
          <span className="badge badge-card">Carta</span>
        </div>

        <p className="meta-subtitle">{buildSubtitle(item) || 'Carta'}</p>
      </div>
    </Link>
  )
}

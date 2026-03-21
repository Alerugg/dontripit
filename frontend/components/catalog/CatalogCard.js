import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'

function resolveHref(item) {
  if (item.type === 'print') return `/prints/${item.id}`
  return `/cards/${item.card_id || item.id}`
}

function buildSubtitle(item) {
  return [
    item.set_name || item.set_code,
    item.collector_number ? `#${item.collector_number}` : null,
    item.language,
    item.variant || item.rarity,
  ].filter(Boolean).join(' · ')
}

export default function CatalogCard({ item, view = 'grid' }) {
  const title = item.name || item.title || 'Elemento sin título'

  return (
    <Link href={resolveHref(item)} className={`catalog-card ${view === 'list' ? 'list' : ''}`}>
      <div className="catalog-image-wrap">
        <FallbackImage
          src={item.primary_image_url}
          alt={title}
          className="catalog-image"
          placeholderClassName="catalog-placeholder image-fallback"
          label={item.game || item.type || 'TCG'}
        />
      </div>

      <div className="catalog-card-content">
        <div className="catalog-card-head">
          <div>
            <p className="meta-game">{item.game || 'TCG'}</p>
            <h3>{title}</h3>
          </div>
          <span className={`badge badge-${item.type || 'card'}`}>{item.type || 'card'}</span>
        </div>

        <p className="meta-subtitle">{buildSubtitle(item) || 'Ficha de catálogo'}</p>
      </div>
    </Link>
  )
}

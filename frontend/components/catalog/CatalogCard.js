import Link from 'next/link'

function resolveHref(item) {
  if (item.type === 'print') return `/prints/${item.id}`
  return `/cards/${item.card_id || item.id}`
}

export default function CatalogCard({ item, view = 'grid' }) {
  const title = item.name || item.title || 'Elemento sin título'
  const subtitle = [item.set_name || item.set_code, item.collector_number ? `#${item.collector_number}` : null]
    .filter(Boolean)
    .join(' · ')

  return (
    <Link href={resolveHref(item)} className={`catalog-card ${view === 'list' ? 'list' : ''}`}>
      <div className="catalog-image-wrap">
        {item.primary_image_url ? (
          <img src={item.primary_image_url} alt={title} className="catalog-image" />
        ) : (
          <div className="catalog-placeholder">Sin imagen</div>
        )}
      </div>

      <div className="catalog-card-content">
        <div className="catalog-card-head">
          <h3>{title}</h3>
          <span className={`badge badge-${item.type || 'card'}`}>{item.type || 'card'}</span>
        </div>

        <p className="meta-game">{item.game || 'TCG'}</p>
        <p className="meta-subtitle">{subtitle || item.variant || 'Ficha de catálogo'}</p>
      </div>
    </Link>
  )
}

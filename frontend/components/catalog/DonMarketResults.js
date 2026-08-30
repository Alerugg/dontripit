import FallbackImage from '../common/FallbackImage'
import { safeCardmarketUrl } from '../../lib/searchV2/market'
import './DonMarketResults.css'

function money(value, currency = 'EUR') {
  const numeric = Number(value)
  if (!Number.isFinite(numeric) || numeric <= 0) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency: currency || 'EUR',
      maximumFractionDigits: 2,
    }).format(numeric)
  } catch {
    return `${numeric.toFixed(2)} ${currency || 'EUR'}`
  }
}

export default function DonMarketResults({ items = [], total = 0 }) {
  return (
    <section className="dri-don-results" aria-label="Resultados DON!!">
      <div className="dri-don-results-head">
        <div>
          <span>One Piece · DON!!</span>
          <h2>DON!! certificados</h2>
        </div>
        <strong>{Number(total || items.length).toLocaleString('es-ES')}</strong>
      </div>
      <p className="dri-don-note">Mostramos identidades DON!! verificadas por personaje desde la fuente de mercado. No inventamos una carta o Print canónico cuando esa relación física todavía no existe.</p>
      <div className="dri-don-grid">
        {items.map((item) => {
          const price = money(item.cardmarket_price, item.cardmarket_currency || 'EUR')
          const cardmarketUrl = safeCardmarketUrl(item.cardmarket_website_path)
          return (
            <article key={`don-${item.metacard_external_id || item.representative_external_product_id}`} className="dri-don-card">
              <div className="dri-don-image-wrap">
                <FallbackImage
                  src={item.primary_image_url}
                  alt={item.name || `DON!! ${item.subject || ''}`}
                  className="catalog-image"
                  placeholderClassName="catalog-placeholder image-fallback"
                  label="DON!!"
                />
                <span className="dri-don-badge">DON!!</span>
              </div>
              <div className="dri-don-copy">
                <span className="dri-don-subject">{item.subject || 'One Piece'}</span>
                <h3>{item.name || `DON!! (${item.subject || 'One Piece'})`}</h3>
                <p>{item.product_count ? `${item.product_count} producto${item.product_count === 1 ? '' : 's'} de mercado asociado${item.product_count === 1 ? '' : 's'}` : 'Identidad de mercado certificada'}</p>
                <div className="dri-don-market">
                  <div>
                    <small>Cardmarket</small>
                    <strong>{price || 'Sin Price Guide actual'}</strong>
                  </div>
                  {cardmarketUrl ? (
                    <a href={cardmarketUrl} target="_blank" rel="noopener noreferrer sponsored">Ver en Cardmarket ↗</a>
                  ) : null}
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}

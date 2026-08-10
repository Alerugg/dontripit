import FallbackImage from '../common/FallbackImage'

function money(value, currency = 'EUR') {
  if (value === null || value === undefined) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value))
  } catch {
    return `${value} ${currency}`
  }
}

export default function MarketProductShelf({ products = [], gameName = 'TCG' }) {
  const current = products.filter((item) => item.listing_status === 'available_verified').slice(0, 8)
  if (!current.length) return null

  return (
    <section className="v4-market-section dri-hub-anchor" id="sellado">
      <div className="v4-section-heading v4-section-heading-small">
        <div>
          <span className="v4-overline"><i /> Cardmarket</span>
          <h2>Producto sellado disponible</h2>
        </div>
        <p>Disponibilidad observada en el último feed. La identidad canónica se muestra por separado.</p>
      </div>

      <div className="v4-market-grid">
        {current.map((item) => {
          const price = money(item.price_low ?? item.price_market ?? item.price_mid, item.currency || 'EUR')
          const verified = item.identity_status === 'verified'
          const content = (
            <>
              <div className="v4-market-image">
                <FallbackImage
                  src={item.primary_image_url}
                  alt={item.name}
                  placeholderClassName="image-fallback"
                  label={gameName}
                />
              </div>
              <div className="v4-market-copy">
                <span className="v4-market-status">Disponible verificado</span>
                <h3>{item.name}</h3>
                <p>{item.category || 'Producto sellado'}</p>
                <div>
                  <strong>{price || 'Sin precio actual'}</strong>
                  <small>{verified ? 'Identidad confirmada' : 'Correspondencia pendiente'}</small>
                </div>
              </div>
            </>
          )

          return item.website_path ? (
            <a
              key={item.external_id}
              href={item.website_path}
              target="_blank"
              rel="noopener noreferrer"
              className="v4-market-card"
            >
              {content}
            </a>
          ) : (
            <article key={item.external_id} className="v4-market-card">{content}</article>
          )
        })}
      </div>
    </section>
  )
}

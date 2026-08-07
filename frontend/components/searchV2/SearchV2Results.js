'use client'

import Link from 'next/link'
import './SearchV2.css'

function badge(value) {
  return value ? <span className="sv2-badge">{value}</span> : null
}

function CardResult({ item, gameSlug, query }) {
  const matched = item.matched_print || {}
  return (
    <Link
      href={`/games/${gameSlug}/cards/${item.card_id}?q=${encodeURIComponent(query || item.name || '')}`}
      className="sv2-result-card"
    >
      <div className="sv2-result-image-wrap">
        {matched.primary_image_url ? (
          <img src={matched.primary_image_url} alt={item.name} className="sv2-result-image" loading="lazy" />
        ) : (
          <div className="sv2-image-placeholder">No image</div>
        )}
      </div>
      <div className="sv2-result-copy">
        <div className="sv2-result-title-row">
          <h3>{item.name}</h3>
          <span className="sv2-variant-count">{item.variant_count || 1} print{item.variant_count === 1 ? '' : 's'}</span>
        </div>
        <p className="sv2-collector-line">
          <strong>{matched.collector_number}</strong>
          {matched.set_code ? <span>{String(matched.set_code).toUpperCase()}</span> : null}
        </p>
        <div className="sv2-badges">
          {badge(matched.rarity)}
          {badge(matched.language?.toUpperCase())}
          {matched.variant_family && matched.variant_family !== 'default' ? badge(matched.variant_family) : null}
          {matched.exact_variant && matched.exact_variant !== 'default' ? badge(matched.exact_variant) : null}
        </div>
      </div>
    </Link>
  )
}

function PrintResult({ item, gameSlug }) {
  return (
    <Link href={`/games/${gameSlug}/cards/${item.card_id}`} className="sv2-result-card sv2-result-card-print">
      <div className="sv2-result-image-wrap">
        {item.primary_image_url ? (
          <img src={item.primary_image_url} alt={item.name} className="sv2-result-image" loading="lazy" />
        ) : (
          <div className="sv2-image-placeholder">No image</div>
        )}
      </div>
      <div className="sv2-result-copy">
        <div className="sv2-result-title-row">
          <h3>{item.name}</h3>
          <span className="sv2-print-pill">Exact print</span>
        </div>
        <p className="sv2-collector-line">
          <strong>{item.collector_number}</strong>
          {item.set_code ? <span>{String(item.set_code).toUpperCase()}</span> : null}
        </p>
        <div className="sv2-badges">
          {badge(item.rarity)}
          {badge(item.language?.toUpperCase())}
          {item.variant_family && item.variant_family !== 'default' ? badge(item.variant_family) : null}
          {item.exact_variant && item.exact_variant !== 'default' ? badge(item.exact_variant) : null}
          {item.attributes?.block ? badge(`Block ${item.attributes.block}`) : null}
        </div>
        {item.releases?.length ? (
          <small className="sv2-release-line">{item.releases[0]}{item.releases.length > 1 ? ` +${item.releases.length - 1}` : ''}</small>
        ) : null}
      </div>
    </Link>
  )
}

export default function SearchV2Results({ items = [], mode = 'normal', gameSlug, query = '', total = null }) {
  const label = mode === 'advanced' ? 'Exact prints' : 'Cards'
  return (
    <section className="sv2-results">
      <div className="sv2-results-head">
        <div>
          <p className="eyebrow">Resultados V2</p>
          <h2>{label}</h2>
        </div>
        <p>{total ?? items.length} resultado{(total ?? items.length) === 1 ? '' : 's'}</p>
      </div>
      <div className="sv2-results-grid">
        {items.map((item) => (
          mode === 'advanced'
            ? <PrintResult key={`print-${item.print_id}`} item={item} gameSlug={gameSlug} />
            : <CardResult key={`card-${item.card_id}`} item={item} gameSlug={gameSlug} query={query} />
        ))}
      </div>
    </section>
  )
}

'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import './SearchV2.css'
import './SearchV2Polish.css'

function badge(value) {
  return value ? <span className="sv2-badge">{value}</span> : null
}

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

function ResultImage({ src, name, gameSlug }) {
  const label = gameSlug === 'onepiece' ? 'One Piece' : gameSlug === 'pokemon' ? 'Pokémon' : gameSlug === 'yugioh' ? 'Yu-Gi-Oh!' : gameSlug === 'mtg' || gameSlug === 'magic' ? 'Magic' : gameSlug || 'TCG'
  const initials = gameSlug === 'onepiece' ? 'OP' : gameSlug === 'pokemon' ? 'PKM' : gameSlug === 'yugioh' ? 'YGO' : gameSlug === 'mtg' || gameSlug === 'magic' ? 'MTG' : undefined
  return (
    <FallbackImage
      src={src}
      alt={name || 'Nombre no disponible'}
      className="sv2-result-image"
      placeholderClassName="sv2-image-placeholder"
      label={label}
      initials={initials}
    />
  )
}

function CardResult({ item, gameSlug, query }) {
  const matched = item.matched_print || {}
  const attrs = item.attributes || {}
  const versions = Number(item.variant_count || 1)
  const cardName = item.name || 'Nombre no disponible'
  const resultLanguage = gameSlug === 'yugioh'
    ? (item.display_language || matched.display_language || matched.language)
    : matched.language

  return (
    <Link
      href={`/games/${gameSlug}/cards/${item.card_id}?q=${encodeURIComponent(query || item.name || '')}`}
      className="sv2-result-card"
    >
      <div className="sv2-result-image-wrap">
        <ResultImage src={matched.primary_image_url} name={cardName} gameSlug={gameSlug} />
      </div>
      <div className="sv2-result-copy">
        <div className="sv2-result-title-row">
          <div>
            <span className="sv2-result-kind">Carta</span>
            <h3>{cardName}</h3>
          </div>
          <span className="sv2-variant-count">{versions} {versions === 1 ? 'versión' : 'versiones'}</span>
        </div>
        <p className="sv2-collector-line">
          <strong>{matched.collector_number}</strong>
          {matched.set_code ? <span>{String(matched.set_code).toUpperCase()}</span> : null}
        </p>
        <div className="sv2-badges">
          {badge(matched.rarity)}
          {badge(resultLanguage?.toUpperCase())}
          {matched.variant_family && matched.variant_family !== 'default' ? badge(matched.variant_family) : null}
          {matched.exact_variant && matched.exact_variant !== 'default' ? badge(matched.exact_variant) : null}
          {gameSlug === 'yugioh' && attrs.card_class ? badge(attrs.card_class) : null}
          {gameSlug === 'yugioh' && attrs.attribute ? badge(attrs.attribute) : null}
          {gameSlug === 'yugioh' && attrs.archetype ? badge(attrs.archetype) : null}
        </div>
        <span className="sv2-result-action">Elegir versión →</span>
      </div>
    </Link>
  )
}

function releaseNames(item, physical) {
  if (Array.isArray(item?.physical_release_names) && item.physical_release_names.length) return item.physical_release_names
  if (Array.isArray(item?.releases) && item.releases.length) return item.releases
  if (Array.isArray(physical?.release_names) && physical.release_names.length) return physical.release_names
  return []
}

function PrintResult({ item, gameSlug }) {
  const physical = item.attributes || {}
  const stamps = Array.isArray(physical.stamps) ? physical.stamps : []
  const printId = item.print_id || item.id
  const href = printId ? `/prints/${printId}` : `/games/${gameSlug}/cards/${item.card_id}`
  const market = item.market || null
  const marketPrice = market?.price || null
  const marketReference = market?.reference || null
  const priceLabel = money(
    marketPrice?.conservative ?? marketPrice?.value ?? marketPrice?.trend ?? marketPrice?.average ?? marketPrice?.minimum,
    marketPrice?.currency || 'EUR',
  )
  const exactReleaseNames = releaseNames(item, physical)
  const cardName = item.name || item.title || 'Nombre no disponible'
  const resultLanguage = gameSlug === 'yugioh'
    ? (item.display_language || item.language)
    : item.language

  return (
    <article className="sv2-result-card sv2-result-card-print">
      <Link href={href} style={{ display: 'contents', color: 'inherit', textDecoration: 'none' }}>
        <div className="sv2-result-image-wrap">
          <ResultImage src={item.primary_image_url} name={cardName} gameSlug={gameSlug} />
        </div>
        <div className="sv2-result-copy">
          <div className="sv2-result-title-row">
            <div>
              <span className="sv2-result-kind is-exact">Versión exacta · Print {printId}</span>
              <h3>{cardName}</h3>
            </div>
          </div>
          <p className="sv2-collector-line">
            <strong>{item.collector_number}</strong>
            {item.set_code ? <span>{String(item.set_code).toUpperCase()}</span> : null}
          </p>
          {exactReleaseNames.length ? (
            <small className="sv2-release-line"><strong>Lanzamiento físico:</strong> {exactReleaseNames[0]}{exactReleaseNames.length > 1 ? ` +${exactReleaseNames.length - 1}` : ''}</small>
          ) : null}
          {item.set_name ? <small className="sv2-release-line"><strong>Set/carta de origen:</strong> {item.set_name}</small> : null}
          <div className="sv2-badges">
            {badge(item.rarity)}
            {badge(resultLanguage?.toUpperCase())}
            {item.variant_family && item.variant_family !== 'default' ? badge(item.variant_family) : null}
            {item.exact_variant && item.exact_variant !== 'default' ? badge(item.exact_variant) : null}
            {gameSlug === 'onepiece' && physical.block ? badge(`Block ${physical.block}`) : null}
            {gameSlug === 'pokemon' && physical.finish ? badge(physical.finish) : null}
            {gameSlug === 'pokemon' && physical.regulation_mark ? badge(`Reg ${physical.regulation_mark}`) : null}
            {gameSlug === 'pokemon' && physical.foil_pattern ? badge(physical.foil_pattern) : null}
            {gameSlug === 'pokemon' ? stamps.slice(0, 2).map((stamp) => <span key={stamp} className="sv2-badge">{stamp}</span>) : null}
            {gameSlug === 'yugioh' && physical.card_class ? badge(physical.card_class) : null}
            {gameSlug === 'yugioh' && physical.attribute ? badge(physical.attribute) : null}
            {gameSlug === 'yugioh' && physical.race ? badge(physical.race) : null}
            {gameSlug === 'yugioh' && physical.atk !== undefined && physical.atk !== null ? badge(`ATK ${physical.atk}`) : null}
            {gameSlug === 'yugioh' && physical.def !== undefined && physical.def !== null ? badge(`DEF ${physical.def}`) : null}
          </div>
          <span className="sv2-result-action">Abrir esta versión exacta →</span>
        </div>
      </Link>

      {market ? (
        <div className="sv2-market-row">
          <strong className="sv2-market-price">{priceLabel || 'Sin Price Guide actual'}</strong>
          <span className="sv2-market-source">Cardmarket</span>
          {marketReference?.id_product ? <span className="sv2-market-id">idProduct {marketReference.id_product}</span> : null}
          {marketReference?.url ? (
            <a
              href={marketReference.url}
              target="_blank"
              rel="noopener noreferrer sponsored"
              className="sv2-result-action sv2-market-link"
            >
              Cardmarket exacto ↗
            </a>
          ) : null}
        </div>
      ) : null}
    </article>
  )
}

export default function SearchV2Results({ items = [], mode = 'normal', gameSlug, query = '', total = null }) {
  const exactPhysicalResults = mode === 'advanced' || (items.length > 0 && items.every((item) => item.type === 'print'))
  const label = exactPhysicalResults ? 'Versiones que coinciden' : 'Cartas encontradas'
  const exactCount = total ?? items.length

  return (
    <section className="sv2-results">
      <div className="sv2-results-head">
        <div>
          <p className="eyebrow">Resultados</p>
          <h2>{label}</h2>
        </div>
        <p>
          {exactPhysicalResults
            ? `${exactCount} resultado${exactCount === 1 ? '' : 's'}`
            : `${items.length} coincidencia${items.length === 1 ? '' : 's'} principal${items.length === 1 ? '' : 'es'}`}
        </p>
      </div>
      {!exactPhysicalResults ? (
        <p className="sv2-results-note">Primero agrupamos por carta. Las singles físicas aparecen aparte y paginadas, para que puedas elegir la edición exacta sin cargar cientos de filas de golpe.</p>
      ) : null}
      <div className="sv2-results-grid">
        {items.map((item) => (
          mode === 'advanced' || item.type === 'print'
            ? <PrintResult key={`print-${item.print_id || item.id}`} item={item} gameSlug={gameSlug} />
            : <CardResult key={`card-${item.card_id}`} item={item} gameSlug={gameSlug} query={query} />
        ))}
      </div>
    </section>
  )
}

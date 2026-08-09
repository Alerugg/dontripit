'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import './SearchV2.css'

function badge(value) {
  return value ? <span className="sv2-badge">{value}</span> : null
}

function ResultImage({ src, name, gameSlug }) {
  const label = gameSlug === 'onepiece' ? 'One Piece' : gameSlug === 'pokemon' ? 'Pokémon' : gameSlug === 'yugioh' ? 'Yu-Gi-Oh!' : gameSlug === 'magic' ? 'Magic' : gameSlug || 'TCG'
  const initials = gameSlug === 'onepiece' ? 'OP' : gameSlug === 'pokemon' ? 'PKM' : gameSlug === 'yugioh' ? 'YGO' : gameSlug === 'magic' ? 'MTG' : undefined
  return (
    <FallbackImage
      src={src}
      alt={name}
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
  const variants = Number(item.variant_count || 1)

  return (
    <Link
      href={`/games/${gameSlug}/cards/${item.card_id}?q=${encodeURIComponent(query || item.name || '')}`}
      className="sv2-result-card"
    >
      <div className="sv2-result-image-wrap">
        <ResultImage src={matched.primary_image_url} name={item.name} gameSlug={gameSlug} />
      </div>
      <div className="sv2-result-copy">
        <div className="sv2-result-title-row">
          <h3>{item.name}</h3>
          <span className="sv2-variant-count">{variants} {variants === 1 ? 'versión' : 'versiones'}</span>
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
          {gameSlug === 'yugioh' && attrs.card_class ? badge(attrs.card_class) : null}
          {gameSlug === 'yugioh' && attrs.attribute ? badge(attrs.attribute) : null}
          {gameSlug === 'yugioh' && attrs.archetype ? badge(attrs.archetype) : null}
        </div>
        <span className="sv2-result-action">Ver {variants === 1 ? 'la versión' : 'versiones'} →</span>
      </div>
    </Link>
  )
}

function PrintResult({ item, gameSlug }) {
  const physical = item.attributes || {}
  const stamps = Array.isArray(physical.stamps) ? physical.stamps : []
  const printId = item.print_id || item.id
  const href = printId ? `/prints/${printId}` : `/games/${gameSlug}/cards/${item.card_id}`

  return (
    <Link href={href} className="sv2-result-card sv2-result-card-print">
      <div className="sv2-result-image-wrap">
        <ResultImage src={item.primary_image_url} name={item.name} gameSlug={gameSlug} />
      </div>
      <div className="sv2-result-copy">
        <div className="sv2-result-title-row">
          <h3>{item.name}</h3>
          <span className="sv2-print-pill">Versión concreta</span>
        </div>
        <p className="sv2-collector-line">
          <strong>{item.collector_number}</strong>
          {item.set_code ? <span>{String(item.set_code).toUpperCase()}</span> : null}
        </p>
        {item.set_name ? <small className="sv2-release-line">{item.set_name}</small> : null}
        <div className="sv2-badges">
          {badge(item.rarity)}
          {badge(item.language?.toUpperCase())}
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
        {item.releases?.length ? (
          <small className="sv2-release-line">{item.releases[0]}{item.releases.length > 1 ? ` +${item.releases.length - 1}` : ''}</small>
        ) : null}
        {gameSlug === 'yugioh' && Array.isArray(physical.release_names) && physical.release_names.length ? (
          <small className="sv2-release-line">{physical.release_names[0]}{physical.release_names.length > 1 ? ` +${physical.release_names.length - 1}` : ''}</small>
        ) : null}
        <span className="sv2-result-action">Abrir esta versión →</span>
      </div>
    </Link>
  )
}

export default function SearchV2Results({ items = [], mode = 'normal', gameSlug, query = '', total = null }) {
  const exactPhysicalResults = mode === 'advanced' || (items.length > 0 && items.every((item) => item.type === 'print'))
  const label = exactPhysicalResults ? 'Versiones que coinciden' : 'Cartas encontradas'
  const count = total ?? items.length

  return (
    <section className="sv2-results">
      <div className="sv2-results-head">
        <div>
          <p className="eyebrow">Resultados</p>
          <h2>{label}</h2>
        </div>
        <p>{count} resultado{count === 1 ? '' : 's'}</p>
      </div>
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

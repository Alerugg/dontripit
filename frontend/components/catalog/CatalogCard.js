import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import { getCardHref, getPrintHref, getSetHref } from '../../lib/catalog/routes'

function releaseCode(item) {
  const release = Array.isArray(item?.physical_releases) ? item.physical_releases[0] : null
  if (release?.code) return String(release.code).toUpperCase()
  const releaseName = item?.physical_release_names?.[0] || release?.name || ''
  const match = String(releaseName).match(/\[([^\]]+)\]/)
  return match?.[1] ? match[1].toUpperCase() : null
}

function setCode(item) {
  return releaseCode(item) || item.set_code?.toUpperCase?.() || item.code?.toUpperCase?.() || item.set_code || item.code || null
}

function formatCurrency(value, currency = 'EUR') {
  const number = value === null || value === undefined || value === '' ? null : Number(value)
  if (number === null || !Number.isFinite(number) || number <= 0) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency: currency || 'EUR',
      maximumFractionDigits: 2,
    }).format(number)
  } catch {
    return `${number.toFixed(2)} ${currency || 'EUR'}`
  }
}

function formatMarketDate(value) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  try {
    return new Intl.DateTimeFormat('es-ES', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    }).format(date)
  } catch {
    return null
  }
}

function buildSubtitle(item) {
  const collectorLabel = item.collector_number ? `#${item.collector_number}` : null

  if (item.type === 'card') {
    const identity = [collectorLabel, setCode(item), item.rarity].filter(Boolean).join(' · ')
    if (identity) return identity
    return 'Carta canónica · elige después la impresión física exacta'
  }

  if (item.type === 'set') {
    return [setCode(item), item.year || item.release_year, item.region].filter(Boolean).join(' · ') || 'Set del catálogo'
  }

  return [
    collectorLabel,
    setCode(item),
    item.language?.toUpperCase?.() || item.language,
    item.region,
  ].filter(Boolean).join(' · ')
}

function buildMetaChips(item) {
  if (item.type === 'card') return []

  if (item.type === 'set') {
    return [item.region].filter(Boolean)
  }

  return [
    item.rarity,
    item.finish && item.finish !== 'default' ? item.finish : null,
    item.variant_label || item.variant,
  ].filter(Boolean)
}

function resolveItemType(item) {
  if (item.type === 'set') return { label: 'Set', className: 'badge-set' }
  if (item.type === 'print') return { label: 'Impresión exacta', className: 'badge-print' }
  return { label: 'Carta canónica', className: 'badge-card' }
}

function exactMarket(item) {
  if (item?.type !== 'print') return null
  const market = item?.market
  if (!market || market.mapping_confidence !== 'exact') return null
  const display = formatCurrency(market.display_price, market.currency || 'EUR')
  if (!display) return null

  return {
    display,
    low: formatCurrency(market.price_low, market.currency || 'EUR'),
    asOf: formatMarketDate(market.as_of),
    printId: market.print_id || item.id || null,
  }
}

function cardCornerMarket(item) {
  if (item?.type !== 'card') return null
  const market = item?.card_market
  if (!market || market.mapping_confidence !== 'exact') return null
  const display = formatCurrency(market.display_price, market.currency || 'EUR')
  if (!display) return null
  return {
    display,
    asOf: formatMarketDate(market.as_of),
    printId: market.print_id || item.matched_print_id || null,
  }
}

function CardSignal({ item }) {
  const count = Number(item.variant_count || 0)
  const market = cardCornerMarket(item)

  if (market) {
    const context = [
      market.printId ? `Print ${market.printId}` : null,
      'Cardmarket',
      market.asOf ? `actualizado ${market.asOf}` : null,
    ].filter(Boolean).join(' · ')

    return (
      <div className="v8-result-signal v8-result-market v15-result-price-card">
        <span className="v8-result-signal-label">Precio de impresión exacta</span>
        <div className="v15-result-price-row">
          <strong>{market.display}</strong>
          {count > 0 ? <span>{count.toLocaleString('es-ES')} impresión{count === 1 ? '' : 'es'}</span> : null}
        </div>
        <span className="v8-result-signal-note">{context}</span>
      </div>
    )
  }

  return (
    <div className="v8-result-signal v8-result-card-signal">
      <div>
        <span className="v8-result-signal-label">Cobertura física</span>
        <strong>{count > 0 ? count.toLocaleString('es-ES') : '—'}</strong>
      </div>
      <span className="v8-result-signal-note">
        {count > 0 ? 'Elige la impresión exacta: el mercado pertenece a cada impresión física' : 'Sin impresiones enlazadas'}
      </span>
    </div>
  )
}

function PrintMarketSignal({ market }) {
  if (!market) return null

  return (
    <div className="v8-result-signal v8-result-market v15-result-price-card">
      <span className="v8-result-signal-label">Precio de impresión exacta</span>
      <div className="v15-result-price-row">
        <strong>{market.display}</strong>
        <span>Cardmarket</span>
      </div>
      <span className="v8-result-signal-note">
        {[market.printId ? `Print ${market.printId}` : null, market.low && market.low !== market.display ? `Low ${market.low}` : null, market.asOf ? `actualizado ${market.asOf}` : null].filter(Boolean).join(' · ')}
      </span>
    </div>
  )
}

function SetSignal({ item }) {
  const code = setCode(item)
  const set = item?.set || null
  const count = Number(item.card_count ?? item.total_cards ?? item.cards_count ?? set?.card_count)
  return (
    <div className="v8-result-signal v8-result-set-signal">
      <span className="v8-result-signal-label">Contenido del set</span>
      <strong>{Number.isFinite(count) && count > 0 ? count.toLocaleString('es-ES') : (code || 'Ver set')}</strong>
      <span className="v8-result-signal-note">{Number.isFinite(count) && count > 0 ? `${count === 1 ? 'carta catalogada' : 'cartas catalogadas'}` : 'Abre para ver cartas e impresiones'}</span>
    </div>
  )
}

function SetCover({ item, title }) {
  const code = setCode(item)
  return (
    <div className="v13-set-cover" aria-hidden="true">
      <span>{code || 'SET'}</span>
      <strong>{title}</strong>
      <small>{item.year || item.release_year || item.region || 'Don’tRipIt'}</small>
    </div>
  )
}

export default function CatalogCard({ item, view = 'grid', queryState, debugImage = false }) {
  const title = item.name || item.title || 'Nombre no disponible'
  const exactPrintId = item.print_id || (item.type === 'print' ? item.id : null)
  const resolvedCardId = item.type === 'card' ? item.id : (item.card_id || null)
  const href = item.type === 'set'
    ? getSetHref(item.game, item.set_code || item.code)
    : exactPrintId
      ? getPrintHref(exactPrintId)
      : resolvedCardId
        ? getCardHref(item.game, resolvedCardId, queryState)
        : '#'
  const itemType = resolveItemType(item)
  const market = exactMarket(item)
  const metaChips = buildMetaChips(item)

  return (
    <Link
      href={href}
      className={`catalog-card v8-result-card v8-result-${item.type || 'card'} ${view === 'list' ? 'list' : ''}`}
      data-result-type={item.type || 'card'}
    >
      <div className="catalog-image-wrap v8-result-image-wrap">
        {item.type === 'set' ? (
          <SetCover item={item} title={title} />
        ) : (
          <FallbackImage
            src={item.primary_image_url}
            alt={title}
            className="catalog-image"
            placeholderClassName="catalog-placeholder image-fallback"
            label={item.game || 'TCG'}
            debug={debugImage}
            debugLabel={item.game === 'onepiece' ? 'One Piece probe' : item.game === 'pokemon' ? 'Pokémon probe' : ''}
          />
        )}
        <span className={`badge ${itemType.className} v8-result-type-badge`}>{itemType.label}</span>
        <span className="v13-result-game-badge">{item.game || 'TCG'}</span>
      </div>

      <div className="catalog-card-content v8-result-content">
        <div className="catalog-card-head v8-result-head">
          <div className="v13-result-inline-identity">
            <span className={`v13-inline-kind ${itemType.className}`}>{itemType.label}</span>
            <span className="meta-game">{item.game || 'TCG'}</span>
          </div>
          <h3>{title}</h3>
        </div>

        <p className="meta-subtitle v8-result-subtitle">{buildSubtitle(item)}</p>

        {metaChips.length > 0 ? (
          <div className="catalog-meta-row v8-result-meta">
            {metaChips.map((meta) => (
              <span key={String(meta)} className="catalog-meta-chip">{meta}</span>
            ))}
          </div>
        ) : <div className="v8-result-meta" aria-hidden="true" />}

        <div className="catalog-card-footer v8-result-footer">
          {item.type === 'print' ? <PrintMarketSignal market={market} /> : null}
          {item.type === 'set' ? <SetSignal item={item} /> : null}
          {item.type !== 'print' && item.type !== 'set' ? <CardSignal item={item} /> : null}
          <span className="v8-result-open">Ver detalle <span aria-hidden="true">→</span></span>
        </div>
      </div>
    </Link>
  )
}

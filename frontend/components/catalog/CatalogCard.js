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

function formatCurrency(value, currency = 'EUR') {
  const number = value === null || value === undefined || value === '' ? null : Number(value)
  if (number === null || !Number.isFinite(number)) return null
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
  if (item.type === 'card') {
    const count = Number(item.variant_count || 0)
    return count > 0
      ? `${count.toLocaleString('es-ES')} impresión${count === 1 ? '' : 'es'} física${count === 1 ? '' : 's'} vinculada${count === 1 ? '' : 's'}`
      : 'Carta canónica · el mercado pertenece a cada impresión física'
  }

  if (item.type === 'set') {
    const code = item.set_code || item.code
    const count = Number(item.card_count ?? item.total_cards ?? item.cards_count)
    if (Number.isFinite(count) && count > 0) {
      return `${count.toLocaleString('es-ES')} carta${count === 1 ? '' : 's'} en el catálogo`
    }
    return code ? `Set ${String(code).toUpperCase()} · abre el set para ver sus cartas e impresiones` : 'Set del catálogo · abre para ver sus cartas e impresiones'
  }

  const collectorLabel = item.collector_number ? `#${item.collector_number}` : null
  const physicalName = item?.physical_release_names?.[0] || item?.physical_releases?.[0]?.name || null
  const origin = item.set_name || item.summary_label || null
  const physical = physicalName ? `Lanzamiento: ${physicalName}` : null
  const originLabel = physicalName && origin ? `Origen: ${origin}` : origin

  return [
    physical,
    originLabel,
    collectorLabel,
    item.language?.toUpperCase?.() || item.language,
    item.variant_label,
  ].filter(Boolean).join(' · ')
}

function buildMetaChips(item) {
  if (item.type === 'card') return []

  if (item.type === 'set') {
    return [
      item.set_code?.toUpperCase?.() || item.code?.toUpperCase?.() || item.set_code || item.code,
      item.year,
      item.region,
    ].filter(Boolean)
  }

  return [
    releaseCode(item) || item.set_code?.toUpperCase?.() || item.set_code,
    item.rarity,
    item.language?.toUpperCase?.() || item.language,
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
  const raw = item?.market?.display_price
  const display = formatCurrency(raw, item?.market?.currency || 'EUR')
  if (!display) return null

  return {
    display,
    low: formatCurrency(item?.market?.price_low, item?.market?.currency || 'EUR'),
    asOf: formatMarketDate(item?.market?.as_of),
  }
}

function CardSignal({ item }) {
  const count = Number(item.variant_count || 0)
  return (
    <div className="v8-result-signal v8-result-card-signal">
      <div>
        <span className="v8-result-signal-label">Cobertura física</span>
        <strong>{count > 0 ? count.toLocaleString('es-ES') : '—'}</strong>
      </div>
      <span className="v8-result-signal-note">{count > 0 ? `impresión${count === 1 ? '' : 'es'}` : 'sin impresiones enlazadas'}</span>
    </div>
  )
}

function PrintMarketSignal({ market }) {
  if (!market) {
    return (
      <div className="v8-result-signal v8-result-market is-empty">
        <span className="v8-result-signal-label">Cardmarket exacto</span>
        <strong>Sin precio actual</strong>
        <span className="v8-result-signal-note">No mostramos estimaciones ni precios de otra edición.</span>
      </div>
    )
  }

  return (
    <div className="v8-result-signal v8-result-market">
      <span className="v8-result-signal-label">Cardmarket exacto</span>
      <strong>{market.display}</strong>
      <span className="v8-result-signal-note">
        {[market.low && market.low !== market.display ? `Low ${market.low}` : null, market.asOf ? `actualizado ${market.asOf}` : null].filter(Boolean).join(' · ') || 'precio vigente de esta impresión'}
      </span>
    </div>
  )
}

function SetSignal({ item }) {
  const code = item.set_code || item.code
  const count = Number(item.card_count ?? item.total_cards ?? item.cards_count)
  return (
    <div className="v8-result-signal v8-result-set-signal">
      <span className="v8-result-signal-label">Contenido del set</span>
      <strong>{Number.isFinite(count) && count > 0 ? count.toLocaleString('es-ES') : (code ? String(code).toUpperCase() : 'Ver set')}</strong>
      <span className="v8-result-signal-note">{Number.isFinite(count) && count > 0 ? `carta${count === 1 ? '' : 's'} catalogada${count === 1 ? '' : 's'}` : 'cartas e impresiones dentro'}</span>
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
        <FallbackImage
          src={item.primary_image_url}
          alt={title}
          className="catalog-image"
          placeholderClassName="catalog-placeholder image-fallback"
          label={item.game || 'TCG'}
          debug={debugImage}
          debugLabel={item.game === 'onepiece' ? 'One Piece probe' : item.game === 'pokemon' ? 'Pokémon probe' : ''}
        />
        <span className={`badge ${itemType.className} v8-result-type-badge`}>{itemType.label}</span>
      </div>

      <div className="catalog-card-content v8-result-content">
        <div className="catalog-card-head v8-result-head">
          <div>
            <p className="meta-game">{item.game || 'TCG'}</p>
            <h3>{title}</h3>
          </div>
        </div>

        <p className="meta-subtitle v8-result-subtitle">{buildSubtitle(item)}</p>

        {metaChips.length > 0 ? (
          <div className="catalog-meta-row v8-result-meta">
            {metaChips.map((meta) => (
              <span key={String(meta)} className="catalog-meta-chip">{meta}</span>
            ))}
          </div>
        ) : null}

        <div className="catalog-card-footer v8-result-footer">
          {item.type === 'print' ? <PrintMarketSignal market={market} /> : null}
          {item.type === 'set' ? <SetSignal item={item} /> : null}
          {item.type !== 'print' && item.type !== 'set' ? <CardSignal item={item} /> : null}
          <span className="v8-result-open">Abrir <span aria-hidden="true">→</span></span>
        </div>
      </div>
    </Link>
  )
}

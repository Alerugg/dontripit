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

function buildSubtitle(item) {
  if (item.type === 'card') {
    const count = Number(item.variant_count || 0)
    return count > 0
      ? `${count} impresión${count === 1 ? '' : 'es'} física${count === 1 ? '' : 's'} disponible${count === 1 ? '' : 's'}`
      : 'Carta canónica · elige después la impresión física'
  }

  if (item.type === 'set') {
    return item.summary_label || item.set_name || item.subtitle || 'Set del catálogo'
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
  return [
    releaseCode(item) || item.set_code?.toUpperCase?.() || item.set_code,
    item.rarity,
    item.language?.toUpperCase?.() || item.language,
    item.finish && item.finish !== 'default' ? item.finish : null,
    item.year,
    item.type === 'print' && item.print_id ? `Print ${item.print_id}` : null,
  ].filter(Boolean)
}

function resolveItemType(item) {
  if (item.type === 'set') return { label: 'Set', className: 'badge-set' }
  if (item.type === 'print') return { label: 'Impresión', className: 'badge-print' }
  return { label: 'Carta', className: 'badge-card' }
}

function formatMarketPrice(item) {
  if (item?.type !== 'print') return null
  const raw = item?.market?.display_price ?? item?.cardmarket_price
  const value = Number(raw)
  if (!Number.isFinite(value)) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency: item?.market?.currency || item?.cardmarket_currency || 'EUR',
      maximumFractionDigits: 2,
    }).format(value)
  } catch {
    return `${value.toFixed(2)} ${item?.market?.currency || item?.cardmarket_currency || 'EUR'}`
  }
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
  const marketPrice = formatMarketPrice(item)
  const physicalCount = item.type === 'card' ? Number(item.variant_count || 0) : 0

  return (
    <Link href={href} className={`catalog-card ${view === 'list' ? 'list' : ''}`}>
      <div className="catalog-image-wrap">
        <FallbackImage
          src={item.primary_image_url}
          alt={title}
          className="catalog-image"
          placeholderClassName="catalog-placeholder image-fallback"
          label={item.game || 'TCG'}
          debug={debugImage}
          debugLabel={item.game === 'onepiece' ? 'One Piece probe' : item.game === 'pokemon' ? 'Pokémon probe' : ''}
        />
      </div>

      <div className="catalog-card-content">
        <div className="catalog-card-head">
          <div>
            <p className="meta-game">{item.game || 'TCG'}</p>
            <h3>{title}</h3>
          </div>
          <span className={`badge ${itemType.className}`}>{itemType.label}</span>
        </div>

        <p className="meta-subtitle">{buildSubtitle(item)}</p>

        <div className="catalog-card-footer">
          <div className="catalog-meta-row">
            {buildMetaChips(item).map((meta) => (
              <span key={meta} className="catalog-meta-chip">{meta}</span>
            ))}
            {marketPrice ? <span className="catalog-meta-chip">Cardmarket {marketPrice}</span> : null}
          </div>
          {physicalCount > 0 ? <span className="catalog-variant-pill">Ver {physicalCount} impresión{physicalCount === 1 ? '' : 'es'}</span> : null}
        </div>
      </div>
    </Link>
  )
}

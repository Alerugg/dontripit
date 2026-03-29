'use client'

import './CatalogCard.css'
import Link from 'next/link'

function toTitleCase(value) {
  return String(value || '')
    .trim()
    .replace(/\s+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function cleanValue(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  if (raw.toLowerCase() === 'unknown') return ''
  if (raw.toLowerCase() === 'default') return ''
  return raw
}

function extractYear(item) {
  const raw =
    item?.set_release_date ||
    item?.release_date ||
    item?.printed_at ||
    item?.date ||
    ''

  if (!raw) return ''

  const year = String(raw).match(/\b(19|20)\d{2}\b/)
  return year ? year[0] : ''
}

function buildHref(item, queryState) {
  if (item?.href) return item.href

  const params = new URLSearchParams()
  if (queryState?.q) params.set('q', queryState.q)
  if (queryState?.type) params.set('type', queryState.type)

  const suffix = params.toString() ? `?${params.toString()}` : ''

  if (item?.type === 'print' || item?.print_id || item?.collector_number) {
    return `/prints/${item?.print_id || item?.id}${suffix}`
  }

  if (item?.game && (item?.card_id || item?.id)) {
    return `/games/${item.game}/cards/${item.card_id || item.id}${suffix}`
  }

  if (item?.game && item?.set_code) {
    return `/games/${item.game}/sets/${encodeURIComponent(String(item.set_code).toLowerCase())}${suffix}`
  }

  return '#'
}

function buildImage(item) {
  return (
    item?.primary_image_url ||
    item?.image_url ||
    item?.image ||
    item?.art_url ||
    ''
  )
}

function buildCollectorLabel(item) {
  const collector = cleanValue(item?.collector_number)
  const total =
    item?.set_card_count ||
    item?.set_total_cards ||
    item?.card_count ||
    item?.total_cards ||
    ''

  if (!collector) return ''
  if (total && Number(total) > 0) return `${collector}/${total}`
  return `#${collector}`
}

function buildFinishLabel(item) {
  const finish = cleanValue(item?.finish)
  if (finish) return toTitleCase(finish)

  if (item?.is_foil === true) return 'Holo'

  const variant = cleanValue(item?.variant)
  if (variant && !['normal', 'base'].includes(variant.toLowerCase())) {
    return toTitleCase(variant)
  }

  return ''
}

function buildRarityLabel(item) {
  const rarity = cleanValue(item?.rarity)
  return rarity ? toTitleCase(rarity) : ''
}

function buildSubtitle(item) {
  const setName = cleanValue(item?.set_name || item?.set || item?.subtitle)
  const year = extractYear(item)

  if (setName && year) return `${setName} (${year})`
  if (setName) return setName
  if (year) return year

  return cleanValue(item?.game)
}

export default function CatalogCard({ item, queryState = {}, view = 'grid' }) {
  const href = buildHref(item, queryState)
  const image = buildImage(item)
  const title = cleanValue(item?.name || item?.title) || 'Carta sin nombre'
  const subtitle = buildSubtitle(item)

  const collector = buildCollectorLabel(item)
  const finish = buildFinishLabel(item)
  const rarity = buildRarityLabel(item)

  const badges = [collector, finish, rarity].filter(Boolean)

  const content = (
    <>
      <div className="catalog-card-media">
        {image ? (
          <img
            src={image}
            alt={title}
            className="catalog-card-image"
            loading="lazy"
          />
        ) : (
          <div className="catalog-card-placeholder">
            <span>{cleanValue(item?.game) || 'card'}</span>
          </div>
        )}
      </div>

      <div className="catalog-card-body">
        <p className="catalog-card-eyebrow">
          {toTitleCase(cleanValue(item?.game) || 'card')}
        </p>

        <h3 className="catalog-card-title">{title}</h3>

        {subtitle ? <p className="catalog-card-subtitle">{subtitle}</p> : null}

        {badges.length ? (
          <div className="catalog-card-badges">
            {badges.map((badge) => (
              <span key={badge} className="catalog-card-badge">
                {badge}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    </>
  )

  return href === '#' ? (
    <article className={`catalog-card ${view === 'list' ? 'is-list' : 'is-grid'}`}>
      {content}
    </article>
  ) : (
    <Link href={href} className={`catalog-card ${view === 'list' ? 'is-list' : 'is-grid'}`}>
      {content}
    </Link>
  )
}
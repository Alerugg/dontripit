'use client'

import FallbackImage from '../common/FallbackImage'

function buildSubtitle(item) {
  return [
    item.set_name || item.set_code,
    item.year,
    item.language,
    item.variant || item.rarity,
  ].filter(Boolean).join(' · ')
}

function buildMeta(item) {
  return [
    item.game || 'Carta',
    item.variant_count ? `${item.variant_count} variante${item.variant_count === 1 ? '' : 's'}` : null,
  ].filter(Boolean).join(' · ')
}

export default function SuggestionRow({ item, active, id, onMouseEnter, onSelect }) {
  const title = item.title || item.name || 'Sin título'
  const subtitle = buildSubtitle(item)

  return (
    <li role="option" aria-selected={active} id={id}>
      <button
        type="button"
        className={`suggestion-row ${active ? 'active' : ''}`}
        onMouseEnter={onMouseEnter}
        onClick={() => onSelect(item)}
      >
        <span className="suggestion-thumb">
          <FallbackImage
            src={item.primary_image_url}
            alt={title}
            className="suggestion-thumb-image"
            placeholderClassName="image-fallback suggestion-thumb-fallback"
            label={item.game || 'Carta'}
          />
        </span>

        <span className="suggestion-copy">
          <strong>{title}</strong>
          <small>{subtitle || 'Carta'}</small>
          <em>{buildMeta(item)}</em>
        </span>
      </button>
    </li>
  )
}

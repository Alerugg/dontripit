'use client'

import Link from 'next/link'
import TypeBadge from './TypeBadge'
import FallbackImage from './common/FallbackImage'

function buildSubtitle(item) {
  if (item.type === 'print') {
    return [item.set_code, item.collector_number ? `#${item.collector_number}` : null, item.variant, item.rarity]
      .filter(Boolean)
      .join(' · ')
  }

  if (item.type === 'set') {
    return item.subtitle || item.set_code || 'Set'
  }

  return item.subtitle || item.game
}

function cardInner(item) {
  return (
    <>
      <div className="result-image-wrap">
        <FallbackImage
          src={item.primary_image_url}
          alt={item.title}
          className="result-image"
          placeholderClassName="result-image-placeholder image-fallback"
          label={item.game || item.type || 'TCG'}
        />
      </div>
      <div className="result-content">
        <div className="result-head">
          <TypeBadge type={item.type} />
          <span className="result-game">{item.game || '-'}</span>
        </div>
        <h3>{item.title}</h3>
        <p>{buildSubtitle(item)}</p>
      </div>
    </>
  )
}

export default function ResultCard({ item }) {
  if (item.type === 'set') {
    return <article className="result-card">{cardInner(item)}</article>
  }

  const href = item.type === 'card' ? `/cards/${item.id}` : `/prints/${item.id}`
  return <Link href={href} className="result-card">{cardInner(item)}</Link>
}

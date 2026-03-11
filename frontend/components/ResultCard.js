'use client'

import Link from 'next/link'

export default function ResultCard({ item }) {
  const wrapperClass = 'catalog-card'

  const content = (
    <>
      <div className="thumb-lg">
        {item.primary_image_url ? <img src={item.primary_image_url} alt={item.title} /> : <span>Sin imagen</span>}
      </div>
      <div className="card-copy">
        <h3>{item.title}</h3>
        <p>{item.game || item.game_slug || '-'}</p>
        <small>
          {item.type.toUpperCase()}
          {item.set_code ? ` · ${item.set_code}` : ''}
          {item.collector_number ? ` · #${item.collector_number}` : ''}
          {item.variant ? ` · ${item.variant}` : ''}
          {item.rarity ? ` · ${item.rarity}` : ''}
        </small>
      </div>
    </>
  )

  if (item.type === 'set') return <article className={wrapperClass}>{content}</article>

  return <Link href={`/explorer/${item.type}/${item.id}`} className={wrapperClass}>{content}</Link>
}

'use client'

import ResultCard from './ResultCard'

export default function CarouselShelf({ title, subtitle, items }) {
  if (!items?.length) return null

  return (
    <section className="panel">
      <div className="panel-header">
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
      <div className="shelf-row">
        {items.map((item) => (
          <div key={`${title}-${item.type}-${item.id}`} className="shelf-item">
            <ResultCard item={item} />
          </div>
        ))}
      </div>
    </section>
  )
}

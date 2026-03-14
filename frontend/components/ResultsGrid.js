'use client'

import ResultCard from './ResultCard'

export default function ResultsGrid({ items, viewMode = 'grid' }) {
  return (
    <section className={`results-grid ${viewMode === 'list' ? 'list' : ''}`}>
      {items.map((item) => <ResultCard key={`${item.type}-${item.id}`} item={item} viewMode={viewMode} />)}
    </section>
  )
}

'use client'

import ResultCard from './ResultCard'

export default function ResultsGrid({ items }) {
  return (
    <section className="results-grid">
      {items.map((item) => <ResultCard key={`${item.type}-${item.id}`} item={item} />)}
    </section>
  )
}

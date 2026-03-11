'use client'

import ResultCard from './ResultCard'

export default function CarouselShelf({ title, subtitle, items }) {
  if (!items?.length) return null

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="mb-3">
        <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
        {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex gap-3 overflow-x-auto pb-2">
        {items.map((item) => (
          <div key={`${title}-${item.type}-${item.id}`} className="min-w-[210px] max-w-[210px] flex-shrink-0">
            <ResultCard item={item} />
          </div>
        ))}
      </div>
    </section>
  )
}

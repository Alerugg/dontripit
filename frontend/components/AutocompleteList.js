'use client'

import Link from 'next/link'

function SuggestionContent({ item }) {
  return (
    <>
      <div className="h-12 w-10 flex-shrink-0 overflow-hidden rounded border border-slate-200 bg-slate-100">
        {item.primary_image_url ? (
          <img src={item.primary_image_url} alt={item.title} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-xs text-slate-500">N/A</div>
        )}
      </div>
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900">{item.title}</p>
        <p className="truncate text-xs text-slate-500">
          {item.type}
          {item.set_code ? ` · Set ${item.set_code}` : ''}
          {item.collector_number ? ` · #${item.collector_number}` : ''}
        </p>
      </div>
    </>
  )
}

export default function AutocompleteList({ items, loading, query, onSelect }) {
  if (!query) return null

  return (
    <div className="absolute z-20 mt-1 max-h-96 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow-lg">
      {loading && <p className="px-4 py-3 text-sm text-slate-500">Buscando sugerencias...</p>}
      {!loading && items.length === 0 && <p className="px-4 py-3 text-sm text-slate-500">Sin sugerencias.</p>}
      {!loading && items.map((item) => {
        if (item.type === 'set') {
          return (
            <div key={`${item.type}-${item.id}`} className="flex items-center gap-3 border-b border-slate-100 px-4 py-2">
              <SuggestionContent item={item} />
            </div>
          )
        }

        return (
          <Link
            key={`${item.type}-${item.id}`}
            href={`/explorer/${item.type}/${item.id}`}
            onClick={() => onSelect?.()}
            className="flex items-center gap-3 border-b border-slate-100 px-4 py-2 transition hover:bg-slate-50"
          >
            <SuggestionContent item={item} />
          </Link>
        )
      })}
    </div>
  )
}

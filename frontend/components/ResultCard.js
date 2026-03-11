'use client'

import Link from 'next/link'

function CardBody({ item }) {
  return (
    <>
      <div className="mb-3 h-56 overflow-hidden rounded-lg bg-slate-100">
        {item.primary_image_url ? (
          <img src={item.primary_image_url} alt={item.title} className="h-full w-full object-cover" />
        ) : (
          <div className="flex h-full items-center justify-center text-sm text-slate-500">Sin imagen</div>
        )}
      </div>
      <h3 className="truncate text-sm font-bold text-slate-900 group-hover:text-blue-700">{item.title}</h3>
      <p className="mt-1 text-xs text-slate-600">Juego: {item.game_slug || item.game || '-'}</p>
      <p className="text-xs text-slate-600">Set: {item.set_code || '-'}</p>
      <p className="text-xs text-slate-600">Collector: {item.collector_number || '-'}</p>
      <p className="text-xs text-slate-600">Variante: {item.variant || '-'}</p>
      <p className="mt-2 text-xs uppercase tracking-wide text-slate-500">{item.type}</p>
    </>
  )
}

export default function ResultCard({ item }) {
  if (item.type === 'set') {
    return (
      <article className="group rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
        <CardBody item={item} />
      </article>
    )
  }

  return (
    <Link href={`/explorer/${item.type}/${item.id}`} className="group rounded-xl border border-slate-200 bg-white p-3 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
      <CardBody item={item} />
    </Link>
  )
}

'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { fetchCardDetail, fetchPrintDetail } from '../../../../lib/apiClient'

const API_KEY_STORAGE = 'tcg_api_key'

export default function DetailPage({ params }) {
  const { type, id } = params
  const [apiKey, setApiKey] = useState('')
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const stored = window.localStorage.getItem(API_KEY_STORAGE) || ''
    setApiKey(stored)
  }, [])

  useEffect(() => {
    if (!apiKey.trim()) {
      setLoading(false)
      setError('Falta API key. Vuelve al explorer y guarda una clave válida.')
      return
    }

    setLoading(true)
    setError('')

    const request = type === 'card'
      ? fetchCardDetail(id, apiKey)
      : type === 'print'
        ? fetchPrintDetail(id, apiKey)
        : Promise.reject(new Error('Tipo no soportado aún para detalle.'))

    request
      .then((payload) => setDetail(payload))
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [apiKey, id, type])

  return (
    <main className="mx-auto min-h-screen max-w-5xl p-6">
      <Link href="/explorer" className="text-sm text-blue-700 underline">← Volver al explorer</Link>

      {loading && <p className="mt-4 text-slate-600">Cargando detalle...</p>}
      {error && <p className="mt-4 rounded border border-red-200 bg-red-50 p-3 text-red-700">{error}</p>}

      {!loading && detail && (
        <section className="mt-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          {type === 'card' && (
            <>
              <h1 className="text-2xl font-bold text-slate-900">{detail.name}</h1>
              <p className="mt-1 text-sm text-slate-600">Juego: {detail.game_slug}</p>
              <h2 className="mt-6 text-lg font-semibold">Prints relacionados</h2>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {(detail.prints || []).map((print) => (
                  <Link key={print.id} href={`/explorer/print/${print.id}`} className="rounded-lg border border-slate-200 p-3 hover:bg-slate-50">
                    <div className="mb-2 h-40 overflow-hidden rounded bg-slate-100">
                      {print.image_url ? <img src={print.image_url} alt={detail.name} className="h-full w-full object-cover" /> : <div className="flex h-full items-center justify-center text-xs text-slate-500">Sin imagen</div>}
                    </div>
                    <p className="text-sm font-semibold">Set {print.set_code}</p>
                    <p className="text-xs text-slate-600">Collector: {print.collector_number || '-'}</p>
                    <p className="text-xs text-slate-600">Variante: {print.variant || '-'}</p>
                  </Link>
                ))}
              </div>
            </>
          )}

          {type === 'print' && (
            <>
              <h1 className="text-2xl font-bold text-slate-900">{detail.card?.name}</h1>
              <p className="mt-1 text-sm text-slate-600">Juego: {detail.game_slug || 'n/a'}</p>
              <div className="mt-4 grid gap-4 md:grid-cols-[320px_1fr]">
                <div className="overflow-hidden rounded-xl border border-slate-200 bg-slate-100">
                  {detail.image_url ? <img src={detail.image_url} alt={detail.card?.name} className="h-full w-full object-cover" /> : <div className="flex h-80 items-center justify-center text-slate-500">Sin imagen</div>}
                </div>
                <div className="space-y-1 text-sm text-slate-700">
                  <p><strong>Tipo:</strong> print</p>
                  <p><strong>Set:</strong> {detail.set?.name} ({detail.set?.code})</p>
                  <p><strong>Collector number:</strong> {detail.collector_number || '-'}</p>
                  <p><strong>Variante:</strong> {detail.variant || '-'}</p>
                  <p><strong>Idioma:</strong> {detail.language || '-'}</p>
                  <p><strong>Rareza:</strong> {detail.rarity || '-'}</p>
                  <p><strong>Foil:</strong> {String(detail.is_foil)}</p>
                </div>
              </div>
            </>
          )}
        </section>
      )}
    </main>
  )
}

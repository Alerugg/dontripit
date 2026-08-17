'use client'

import { useState } from 'react'

export default function LibraryActions({ printId }) {
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState('')

  async function add(kind) {
    setBusy(kind)
    setStatus('')
    try {
      const response = await fetch(`/api/library/${kind}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(kind === 'collection' ? { print_id: Number(printId), quantity: 1 } : { print_id: Number(printId) }),
      })
      const payload = await response.json().catch(() => ({}))
      if (response.status === 401) {
        window.location.assign(`/login?next=${encodeURIComponent(window.location.pathname)}`)
        return
      }
      if (!response.ok) throw new Error(payload.message || 'No pudimos guardar esta edición.')
      setStatus(kind === 'collection' ? 'Añadida a tu colección.' : 'Añadida a tu wishlist.')
    } catch (error) {
      setStatus(error.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="detail-actions" aria-live="polite">
      <button type="button" className="dri-btn dri-btn-primary" onClick={() => add('collection')} disabled={Boolean(busy)}>
        {busy === 'collection' ? 'Guardando…' : '+ Mi colección'}
      </button>
      <button type="button" className="dri-btn dri-btn-ghost" onClick={() => add('wishlist')} disabled={Boolean(busy)}>
        {busy === 'wishlist' ? 'Guardando…' : '♡ Wishlist'}
      </button>
      {status ? <small>{status}</small> : null}
    </div>
  )
}

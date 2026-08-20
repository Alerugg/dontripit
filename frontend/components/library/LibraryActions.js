'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'

async function readMembership(kind, printId) {
  try {
    const response = await fetch(`/api/library/${kind}`, { cache: 'no-store' })
    if (!response.ok) return null
    const payload = await response.json().catch(() => ({}))
    return (payload?.items || []).find((item) => Number(item?.print?.id) === Number(printId)) || null
  } catch {
    return null
  }
}

export default function LibraryActions({ printId }) {
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState('')
  const [checking, setChecking] = useState(true)
  const [collectionEntry, setCollectionEntry] = useState(null)
  const [wishlistEntry, setWishlistEntry] = useState(null)

  useEffect(() => {
    if (!printId) {
      setChecking(false)
      return undefined
    }

    let cancelled = false
    setChecking(true)
    Promise.all([
      readMembership('collection', printId),
      readMembership('wishlist', printId),
    ]).then(([collection, wishlist]) => {
      if (!cancelled) {
        setCollectionEntry(collection)
        setWishlistEntry(wishlist)
      }
    }).finally(() => {
      if (!cancelled) setChecking(false)
    })

    return () => { cancelled = true }
  }, [printId])

  async function add(kind) {
    if (kind === 'collection' && collectionEntry) {
      setStatus('Esta Print ya está en tu colección. Ajusta la cantidad desde Mi colección.')
      return
    }
    if (kind === 'wishlist' && wishlistEntry) {
      setStatus('Esta Print ya está en tu wishlist. Conservamos su prioridad y precio objetivo.')
      return
    }

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

      if (kind === 'collection') {
        setCollectionEntry({ print: { id: Number(printId) }, quantity: 1 })
        setStatus('Añadida a tu colección.')
      } else {
        setWishlistEntry({ print: { id: Number(printId) }, priority: 0, target_price: null, target_currency: null })
        setStatus('Añadida a tu wishlist.')
      }
    } catch (error) {
      setStatus(error.message)
    } finally {
      setBusy('')
    }
  }

  return (
    <div className="detail-actions" aria-live="polite">
      {collectionEntry ? (
        <Link href="/collection" className="dri-btn dri-btn-primary">
          En colección · {Number(collectionEntry.quantity || 1)} ✓
        </Link>
      ) : (
        <button type="button" className="dri-btn dri-btn-primary" onClick={() => add('collection')} disabled={Boolean(busy) || checking}>
          {busy === 'collection' ? 'Guardando…' : checking ? 'Comprobando…' : '+ Mi colección'}
        </button>
      )}

      {wishlistEntry ? (
        <Link href="/wishlist" className="dri-btn dri-btn-ghost">En wishlist ✓</Link>
      ) : (
        <button type="button" className="dri-btn dri-btn-ghost" onClick={() => add('wishlist')} disabled={Boolean(busy) || checking}>
          {busy === 'wishlist' ? 'Guardando…' : checking ? 'Comprobando…' : '♡ Wishlist'}
        </button>
      )}
      {status ? <small>{status}</small> : null}
    </div>
  )
}

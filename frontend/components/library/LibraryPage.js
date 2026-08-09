'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import './LibraryPage.css'

function money(price) {
  if (!price?.value || !price?.currency) return null
  try {
    return new Intl.NumberFormat('es-ES', { style: 'currency', currency: price.currency, maximumFractionDigits: 2 }).format(price.value)
  } catch {
    return `${price.value} ${price.currency}`
  }
}

function LibraryCard({ item, kind, onRemove, onQuantity }) {
  const print = item.print || {}
  const latest = money(item.latest_price)
  return (
    <article className="library-card">
      <Link href={`/prints/${print.id}`} className="library-card-media">
        <FallbackImage
          src={print.image_url}
          alt={print.card_name || 'Carta'}
          className="detail-image"
          placeholderClassName="image-fallback"
          label={print.game || 'TCG'}
        />
      </Link>
      <div className="library-card-body">
        <Link href={`/prints/${print.id}`} className="library-card-title">{print.card_name || 'Carta'}</Link>
        <p className="library-card-meta">
          {[print.set_code?.toUpperCase(), print.collector_number, print.language?.toUpperCase(), print.rarity, print.variant !== 'default' ? print.variant : null].filter(Boolean).join(' · ')}
        </p>
        <div className="library-price">
          <strong>{latest || 'Precio pendiente'}</strong>
          <small>{latest ? `${item.latest_price?.source || 'Fuente registrada'}${item.latest_price?.as_of ? ` · ${new Date(item.latest_price.as_of).toLocaleDateString('es-ES')}` : ''}` : 'No mostramos estimaciones sin fuente verificada.'}</small>
        </div>
        <div className="library-card-actions">
          {kind === 'collection' ? (
            <>
              <span className="library-qty">Tienes {item.quantity}</span>
              <button type="button" className="dri-btn dri-btn-ghost" onClick={() => onQuantity(item, Math.max(1, item.quantity - 1))} disabled={item.quantity <= 1}>−</button>
              <button type="button" className="dri-btn dri-btn-ghost" onClick={() => onQuantity(item, item.quantity + 1)}>+</button>
            </>
          ) : null}
          <button type="button" className="dri-btn dri-btn-ghost" onClick={() => onRemove(item)}>Quitar</button>
        </div>
      </div>
    </article>
  )
}

export default function LibraryPage({ kind = 'collection' }) {
  const isCollection = kind === 'collection'
  const [data, setData] = useState({ items: [], count: 0, known_value_eur: 0 })
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')

  const endpoint = `/api/library/${kind}`

  async function load() {
    setLoading(true)
    try {
      const response = await fetch(endpoint, { cache: 'no-store' })
      if (response.status === 401) {
        window.location.assign('/login')
        return
      }
      const payload = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error('No pudimos cargar tus cartas.')
      setData(payload)
    } catch (error) {
      setMessage(error.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [endpoint])

  const pieces = useMemo(
    () => isCollection ? data.items.reduce((total, item) => total + Number(item.quantity || 0), 0) : data.count,
    [data, isCollection],
  )

  async function remove(item) {
    setMessage('')
    const response = await fetch(endpoint, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ print_id: item.print.id }),
    })
    if (!response.ok) {
      setMessage('No pudimos quitar esta carta. Inténtalo de nuevo.')
      return
    }
    await load()
  }

  async function changeQuantity(item, quantity) {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ print_id: item.print.id, quantity }),
    })
    if (!response.ok) {
      setMessage('No pudimos actualizar la cantidad.')
      return
    }
    await load()
  }

  return (
    <main>
      <TopNav />
      <section className="library-shell">
        <header className="library-hero">
          <div>
            <span className="dri-kicker">Tu espacio</span>
            <h1>{isCollection ? 'Mi colección' : 'Mi wishlist'}</h1>
            <p>{isCollection ? 'Cada arte, idioma y edición física vive por separado. Así sabes exactamente qué tienes.' : 'Guarda exactamente la edición que buscas. Sin confundir una carta con otra variante.'}</p>
          </div>
          <div className="library-summary">
            <div className="library-stat"><span>{isCollection ? 'Ediciones' : 'Deseos'}</span><strong>{data.count || 0}</strong></div>
            {isCollection ? <div className="library-stat"><span>Cartas físicas</span><strong>{pieces}</strong></div> : null}
            {isCollection ? <div className="library-stat"><span>Valor conocido*</span><strong>{new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(data.known_value_eur || 0)}</strong></div> : null}
          </div>
        </header>

        {isCollection ? <p className="detail-meta">*Solo suma precios en EUR con una fuente registrada. Las cartas sin precio verificado no se estiman.</p> : null}
        {message ? <div className="library-message">{message}</div> : null}
        {loading ? <div className="library-loading">Cargando tu colección…</div> : null}

        {!loading && !data.items?.length ? (
          <div className="library-empty">
            <span className="dri-kicker">Empieza por una carta</span>
            <h2>{isCollection ? 'Tu colección está esperando.' : 'Tu wishlist está vacía.'}</h2>
            <p>Busca una carta, abre la edición exacta que te interesa y guárdala con un toque.</p>
            <Link href="/dashboard" className="dri-btn dri-btn-primary">Explorar cartas</Link>
          </div>
        ) : null}

        {!loading && data.items?.length ? (
          <div className="library-grid">
            {data.items.map((item) => (
              <LibraryCard key={item.id} item={item} kind={kind} onRemove={remove} onQuantity={changeQuantity} />
            ))}
          </div>
        ) : null}
      </section>
    </main>
  )
}

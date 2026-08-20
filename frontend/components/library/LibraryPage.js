'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import './LibraryPage.css'

function money(value, currency = 'EUR') {
  if (value === null || value === undefined || value === '' || !currency) return null
  const number = Number(value)
  if (!Number.isFinite(number)) return null
  try {
    return new Intl.NumberFormat('es-ES', { style: 'currency', currency, maximumFractionDigits: 2 }).format(number)
  } catch {
    return `${number.toFixed(2)} ${currency}`
  }
}

function shortDate(value) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  try {
    return new Intl.DateTimeFormat('es-ES', { day: 'numeric', month: 'short', year: 'numeric' }).format(date)
  } catch {
    return null
  }
}

function searchableText(item) {
  const print = item?.print || {}
  return [
    print.card_name,
    print.game,
    print.set_code,
    print.set_name,
    print.collector_number,
    print.language,
    print.rarity,
    print.variant,
    print.id,
  ].filter(Boolean).join(' ').toLowerCase()
}

function valuationNumber(item) {
  const value = item?.latest_price?.valuation_value ?? item?.latest_price?.conservative
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function LibraryCard({ item, kind, onRemove, onQuantity }) {
  const isCollection = kind === 'collection'
  const print = item.print || {}
  const price = item.latest_price || null
  const conservativeValue = valuationNumber(item)
  const conservative = money(conservativeValue, price?.currency)
  const observed = money(price?.value, price?.currency)
  const trend = money(price?.trend, price?.currency)
  const average = money(price?.average, price?.currency)
  const minimum = money(price?.minimum, price?.currency)
  const quantity = Number(item.quantity || 1)
  const positionValue = conservativeValue !== null ? money(conservativeValue * quantity, price?.currency) : null
  const marketDate = shortDate(price?.as_of)
  const purchasePrice = money(item.purchase_price, item.purchase_currency)
  const acquiredAt = shortDate(item.acquired_at)
  const physicalMeta = [
    print.set_code?.toUpperCase(),
    print.collector_number ? `#${print.collector_number}` : null,
    print.language?.toUpperCase(),
    print.rarity,
    print.is_foil ? 'Foil' : null,
    print.variant && print.variant !== 'default' ? print.variant : null,
  ].filter(Boolean)

  return (
    <article className={`library-card v10-library-card ${conservative ? 'is-valued' : 'is-unvalued'}`}>
      <Link href={`/prints/${print.id}`} className="library-card-media v10-library-media">
        <FallbackImage
          src={print.image_url}
          alt={print.card_name || 'Carta'}
          className="detail-image"
          placeholderClassName="image-fallback"
          label={print.game || 'TCG'}
        />
        <span className="v10-print-badge">Print {print.id}</span>
      </Link>

      <div className="library-card-body v10-library-body">
        <div className="v10-library-title-block">
          <p className="v10-library-game">{print.game || 'TCG'}</p>
          <Link href={`/prints/${print.id}`} className="library-card-title">{print.card_name || 'Carta'}</Link>
          <div className="v10-physical-meta" aria-label="Identidad física">
            {physicalMeta.map((meta) => <span key={String(meta)}>{meta}</span>)}
          </div>
        </div>

        <div className={`library-price v10-library-price ${conservative ? 'is-valued' : ''}`}>
          <div className="v10-price-head">
            <span className="library-price-label">{conservative ? 'Valor conservador · unidad' : observed ? 'Precio actual observado' : 'Mercado exacto'}</span>
            {price?.source ? <span className="v10-source-pill">{price.source}</span> : null}
          </div>
          <strong>{conservative || observed || 'Sin precio verificado'}</strong>

          {isCollection && positionValue && quantity > 1 ? (
            <div className="v10-position-value">
              <span>Posición · {quantity} × {conservative}</span>
              <strong>{positionValue}</strong>
            </div>
          ) : null}

          {price ? (
            <>
              {(minimum || trend || average) ? (
                <small className="library-price-metrics">
                  {[minimum ? `Mín ${minimum}` : null, trend ? `Tend ${trend}` : null, average ? `Media ${average}` : null].filter(Boolean).join(' · ')}
                </small>
              ) : null}
              <small>
                {marketDate ? `Actualizado ${marketDate}` : 'Precio actual de fuente registrada'}
                {!conservative ? ' · No suma al valor conservador' : ''}
              </small>
            </>
          ) : (
            <small>Esta Print no se estima con otra edición. Se valorará cuando exista un precio fiable para esta identidad exacta.</small>
          )}
        </div>

        {isCollection && (item.condition || purchasePrice || acquiredAt) ? (
          <div className="v10-owned-meta">
            {item.condition ? <span>Estado <strong>{item.condition}</strong></span> : null}
            {purchasePrice ? <span>Compra <strong>{purchasePrice}</strong></span> : null}
            {acquiredAt ? <span>Adquirida <strong>{acquiredAt}</strong></span> : null}
          </div>
        ) : null}

        <div className="library-card-actions v10-library-actions">
          {isCollection ? (
            <div className="v10-quantity-control" aria-label={`Cantidad de ${print.card_name || 'esta carta'}`}>
              <span className="library-qty">Cantidad <strong>{quantity}</strong></span>
              <button type="button" className="v10-qty-button" onClick={() => onQuantity(item, Math.max(1, quantity - 1))} disabled={quantity <= 1} aria-label={`Restar una unidad de ${print.card_name || 'esta carta'}`}>−</button>
              <button type="button" className="v10-qty-button" onClick={() => onQuantity(item, quantity + 1)} aria-label={`Sumar una unidad de ${print.card_name || 'esta carta'}`}>+</button>
            </div>
          ) : null}
          <Link href={`/prints/${print.id}`} className="v10-open-print">Ver Print →</Link>
          <button type="button" className="v10-remove-button" onClick={() => onRemove(item)}>Quitar</button>
        </div>
      </div>
    </article>
  )
}

export default function LibraryPage({ kind = 'collection' }) {
  const isCollection = kind === 'collection'
  const [data, setData] = useState({ items: [], count: 0, known_value_eur: 0, valuation_coverage_count: 0 })
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('recent')

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
      setMessage('')
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

  const coverageCount = Number(data.valuation_coverage_count || 0)
  const totalVersions = Number(data.count || 0)
  const unvaluedCount = Math.max(0, totalVersions - coverageCount)
  const coveragePercent = totalVersions > 0 ? Math.round((coverageCount / totalVersions) * 100) : 0

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    const filtered = (data.items || []).filter((item) => !normalized || searchableText(item).includes(normalized))
    if (!isCollection) return filtered

    return [...filtered].sort((left, right) => {
      if (sort === 'name') return String(left?.print?.card_name || '').localeCompare(String(right?.print?.card_name || ''), 'es', { sensitivity: 'base' })
      if (sort === 'value_desc') {
        const leftValue = valuationNumber(left)
        const rightValue = valuationNumber(right)
        if (leftValue === null && rightValue === null) return 0
        if (leftValue === null) return 1
        if (rightValue === null) return -1
        return (rightValue * Number(right.quantity || 1)) - (leftValue * Number(left.quantity || 1))
      }
      if (sort === 'quantity_desc') return Number(right.quantity || 0) - Number(left.quantity || 0)
      return 0
    })
  }, [data.items, isCollection, query, sort])

  async function remove(item) {
    const cardName = item?.print?.card_name || 'esta carta'
    if (typeof window !== 'undefined' && !window.confirm(`¿Quitar ${cardName} de ${isCollection ? 'tu colección' : 'tu wishlist'}?`)) return
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
      <section className={`library-shell v10-library-shell ${isCollection ? 'v10-collection-shell' : 'v10-wishlist-shell'}`}>
        <header className="library-hero v10-library-hero">
          <div>
            <span className="dri-kicker">{isCollection ? 'Portfolio físico' : 'Tu wishlist'}</span>
            <h1>{isCollection ? 'Mi colección' : 'Wishlist'}</h1>
            <p>{isCollection ? 'Cada posición corresponde a una Print física exacta. El total solo suma versiones con valoración conservadora Cardmarket verificable.' : 'Las versiones que quieres encontrar, sin mezclarlas con otras ediciones.'}</p>
            <div className="ux-library-actions">
              <Link href="/explorer" className="dri-btn dri-btn-primary">Buscar cartas</Link>
              <Link href={isCollection ? '/wishlist' : '/collection'} className="dri-btn dri-btn-ghost">{isCollection ? 'Ver wishlist' : 'Ver colección'}</Link>
            </div>
          </div>

          <div className={`library-summary v10-library-summary ${isCollection ? 'is-collection' : ''}`}>
            <div className="library-stat"><span>{isCollection ? 'Prints distintas' : 'En wishlist'}</span><strong>{totalVersions.toLocaleString('es-ES')}</strong></div>
            {isCollection ? <div className="library-stat"><span>Cartas físicas</span><strong>{pieces.toLocaleString('es-ES')}</strong></div> : null}
            {isCollection ? <div className="library-stat v10-value-stat"><span>Valor conservador</span><strong>{money(data.known_value_eur || 0, 'EUR')}</strong></div> : null}
            {isCollection ? <div className="library-stat"><span>Cobertura valoración</span><strong>{coveragePercent}%</strong><small>{coverageCount} / {totalVersions}</small></div> : null}
          </div>
        </header>

        {isCollection ? (
          <section className="v10-coverage-panel" aria-label="Cobertura de valoración">
            <div>
              <span>Cobertura exacta de valoración</span>
              <strong>{coverageCount.toLocaleString('es-ES')} valorada{coverageCount === 1 ? '' : 's'} · {unvaluedCount.toLocaleString('es-ES')} sin valorar</strong>
              <small>Solo Cardmarket actual de cada Print exacta. Las versiones sin valor conservador no se estiman ni se suman.</small>
            </div>
            <div className="v10-coverage-meter" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={coveragePercent} aria-label={`Cobertura de valoración ${coveragePercent}%`}>
              <span style={{ width: `${coveragePercent}%` }} />
            </div>
          </section>
        ) : null}

        {isCollection && !loading && data.items?.length ? (
          <div className="v10-library-toolbar">
            <label>
              <span className="v10-sr-only">Filtrar colección</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Carta, set, número, idioma o Print ID…" />
            </label>
            <select value={sort} onChange={(event) => setSort(event.target.value)} aria-label="Ordenar colección">
              <option value="recent">Más recientes</option>
              <option value="name">Nombre A–Z</option>
              <option value="value_desc">Valor conservador ↓</option>
              <option value="quantity_desc">Cantidad ↓</option>
            </select>
            <span>{visibleItems.length.toLocaleString('es-ES')} de {totalVersions.toLocaleString('es-ES')} Prints</span>
          </div>
        ) : null}

        {message ? <div className="library-message">{message}</div> : null}
        {loading ? <div className="library-loading">Cargando tus cartas…</div> : null}

        {!loading && !data.items?.length ? (
          <div className="library-empty">
            <span className="dri-kicker">Empieza por una Print</span>
            <h2>{isCollection ? 'Tu colección está vacía.' : 'Tu wishlist está vacía.'}</h2>
            <p>Busca una carta, elige la impresión física exacta que te interesa y guárdala.</p>
            <Link href="/explorer" className="dri-btn dri-btn-primary">Explorar catálogo</Link>
          </div>
        ) : null}

        {!loading && data.items?.length && !visibleItems.length ? (
          <div className="library-empty v10-filter-empty">
            <span className="dri-kicker">Sin coincidencias</span>
            <h2>No encontramos esa Print en tu colección.</h2>
            <p>Prueba otro nombre, set, idioma o número de coleccionista.</p>
            <button type="button" className="dri-btn dri-btn-ghost" onClick={() => setQuery('')}>Limpiar búsqueda</button>
          </div>
        ) : null}

        {!loading && visibleItems.length ? (
          <div className="library-grid v10-library-grid">
            {visibleItems.map((item) => (
              <LibraryCard key={item.id} item={item} kind={kind} onRemove={remove} onQuantity={changeQuantity} />
            ))}
          </div>
        ) : null}
      </section>
    </main>
  )
}

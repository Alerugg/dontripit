'use client'

import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'
import TopNav from '../layout/TopNav'
import FallbackImage from '../common/FallbackImage'
import './LibraryPage.css'
import './LibraryWishlist.css'

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

function numericOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
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
  return numericOrNull(item?.latest_price?.valuation_value ?? item?.latest_price?.conservative)
}

function wishlistComparison(item) {
  const current = numericOrNull(item?.latest_price?.value)
  const target = numericOrNull(item?.target_price)
  const currentCurrency = String(item?.latest_price?.currency || '').toUpperCase()
  const targetCurrency = String(item?.target_currency || '').toUpperCase()
  const comparable = current !== null && target !== null && currentCurrency && targetCurrency && currentCurrency === targetCurrency

  return {
    current,
    target,
    currentCurrency,
    targetCurrency,
    comparable,
    reached: comparable ? current <= target : false,
    difference: comparable ? current - target : null,
  }
}

function PriorityBadge({ value }) {
  const priority = Math.max(0, Math.min(3, Number(value || 0)))
  return (
    <span className={`v11-priority-badge priority-${priority}`}>
      {priority === 0 ? 'Sin prioridad' : `Prioridad ${priority}/3`}
    </span>
  )
}

function WishlistPlan({ item, onUpdate }) {
  const [priority, setPriority] = useState(String(Number(item.priority || 0)))
  const [target, setTarget] = useState(item.target_price ?? '')
  const [currency, setCurrency] = useState(String(item.target_currency || item.latest_price?.currency || 'EUR').toUpperCase())
  const [saving, setSaving] = useState(false)
  const comparison = wishlistComparison(item)
  const targetDisplay = money(item.target_price, item.target_currency)
  const currentDisplay = money(comparison.current, comparison.currentCurrency)
  const differenceDisplay = comparison.comparable ? money(Math.abs(comparison.difference), comparison.currentCurrency) : null

  async function save(event) {
    event.preventDefault()
    setSaving(true)
    try {
      await onUpdate(item, {
        priority: Number(priority),
        target_price: target === '' ? null : Number(target),
        target_currency: target === '' ? null : currency,
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="v11-wishlist-plan">
      <div className="v11-target-summary">
        <div>
          <span>Precio actual exacto</span>
          <strong>{currentDisplay || 'Sin precio actual'}</strong>
        </div>
        <div>
          <span>Tu objetivo</span>
          <strong>{targetDisplay || 'Sin objetivo'}</strong>
        </div>
      </div>

      {comparison.comparable ? (
        <div className={`v11-target-status ${comparison.reached ? 'is-reached' : 'is-above'}`}>
          <strong>{comparison.reached ? 'Objetivo alcanzado' : `${differenceDisplay} sobre tu objetivo`}</strong>
          <span>{comparison.reached ? 'El precio actual exacto está en o por debajo del objetivo que guardaste.' : 'El precio actual exacto todavía está por encima de tu objetivo.'}</span>
        </div>
      ) : (
        <div className="v11-target-status is-unknown">
          <strong>No comparable</strong>
          <span>
            {comparison.current === null
              ? 'No existe un precio Cardmarket actual para esta Print exacta.'
              : comparison.target === null
                ? 'Añade un precio objetivo para poder comparar.'
                : 'Precio actual y objetivo usan monedas distintas; no aplicamos FX implícito.'}
          </span>
        </div>
      )}

      <details className="v11-target-editor">
        <summary>Editar objetivo y prioridad</summary>
        <form onSubmit={save}>
          <label>
            <span>Prioridad</span>
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="0">Sin prioridad</option>
              <option value="1">Prioridad 1/3</option>
              <option value="2">Prioridad 2/3</option>
              <option value="3">Prioridad 3/3</option>
            </select>
          </label>
          <label>
            <span>Precio objetivo</span>
            <input
              type="number"
              min="0"
              step="0.01"
              inputMode="decimal"
              value={target}
              onChange={(event) => setTarget(event.target.value)}
              placeholder="Sin objetivo"
            />
          </label>
          <label>
            <span>Moneda</span>
            <select value={currency} onChange={(event) => setCurrency(event.target.value)} disabled={target === ''}>
              <option value="EUR">EUR</option>
              <option value="USD">USD</option>
              <option value="GBP">GBP</option>
              <option value="JPY">JPY</option>
            </select>
          </label>
          <button type="submit" disabled={saving}>{saving ? 'Guardando…' : 'Guardar'}</button>
        </form>
      </details>
    </div>
  )
}

function LibraryCard({ item, kind, onRemove, onQuantity, onWishlistUpdate }) {
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
    <article className={`library-card v10-library-card ${isCollection ? 'v10-collection-card' : 'v11-wishlist-card'} ${conservative ? 'is-valued' : 'is-unvalued'}`}>
      <Link href={`/prints/${print.id}`} className="library-card-media v10-library-media">
        <FallbackImage
          src={print.image_url}
          alt={print.card_name || 'Carta'}
          className="detail-image"
          placeholderClassName="image-fallback"
          label={print.game || 'TCG'}
        />
        <span className="v10-print-badge">Print {print.id}</span>
        {!isCollection ? <PriorityBadge value={item.priority} /> : null}
      </Link>

      <div className="library-card-body v10-library-body">
        <div className="v10-library-title-block">
          <p className="v10-library-game">{print.game || 'TCG'}</p>
          <Link href={`/prints/${print.id}`} className="library-card-title">{print.card_name || 'Carta'}</Link>
          <div className="v10-physical-meta" aria-label="Identidad física">
            {physicalMeta.map((meta) => <span key={String(meta)}>{meta}</span>)}
          </div>
        </div>

        {isCollection ? (
          <div className={`library-price v10-library-price ${conservative ? 'is-valued' : ''}`}>
            <div className="v10-price-head">
              <span className="library-price-label">{conservative ? 'Valor conservador · unidad' : observed ? 'Precio actual observado' : 'Mercado exacto'}</span>
              {price?.source ? <span className="v10-source-pill">{price.source}</span> : null}
            </div>
            <strong>{conservative || observed || 'Sin precio verificado'}</strong>

            {positionValue && quantity > 1 ? (
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
        ) : (
          <WishlistPlan item={item} onUpdate={onWishlistUpdate} />
        )}

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

  const wishlistStats = useMemo(() => {
    if (isCollection) return { priced: 0, targeted: 0, reached: 0 }
    return (data.items || []).reduce((stats, item) => {
      const comparison = wishlistComparison(item)
      if (comparison.current !== null) stats.priced += 1
      if (comparison.target !== null) stats.targeted += 1
      if (comparison.reached) stats.reached += 1
      return stats
    }, { priced: 0, targeted: 0, reached: 0 })
  }, [data.items, isCollection])

  const visibleItems = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    const filtered = (data.items || []).filter((item) => !normalized || searchableText(item).includes(normalized))

    return [...filtered].sort((left, right) => {
      if (sort === 'name') return String(left?.print?.card_name || '').localeCompare(String(right?.print?.card_name || ''), 'es', { sensitivity: 'base' })

      if (isCollection) {
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
      }

      if (sort === 'priority_desc') return Number(right.priority || 0) - Number(left.priority || 0)
      if (sort === 'target_status') {
        const leftComparison = wishlistComparison(left)
        const rightComparison = wishlistComparison(right)
        const leftRank = leftComparison.reached ? 0 : leftComparison.comparable ? 1 : 2
        const rightRank = rightComparison.reached ? 0 : rightComparison.comparable ? 1 : 2
        if (leftRank !== rightRank) return leftRank - rightRank
        if (leftComparison.comparable && rightComparison.comparable) return leftComparison.difference - rightComparison.difference
        return Number(right.priority || 0) - Number(left.priority || 0)
      }
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

  async function updateWishlist(item, values) {
    setMessage('')
    if (values.target_price !== null && (!Number.isFinite(values.target_price) || values.target_price < 0)) {
      setMessage('El precio objetivo debe ser un número igual o mayor que 0.')
      return
    }
    const response = await fetch('/api/library/wishlist', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        print_id: item.print.id,
        priority: values.priority,
        target_price: values.target_price,
        target_currency: values.target_currency,
      }),
    })
    if (!response.ok) {
      setMessage('No pudimos guardar el objetivo de esta Print.')
      return
    }
    await load()
  }

  return (
    <main>
      <TopNav />
      <section className={`library-shell v10-library-shell ${isCollection ? 'v10-collection-shell' : 'v11-wishlist-shell'}`}>
        <header className="library-hero v10-library-hero">
          <div>
            <span className="dri-kicker">{isCollection ? 'Portfolio físico' : 'Radar de compra'}</span>
            <h1>{isCollection ? 'Mi colección' : 'Wishlist'}</h1>
            <p>{isCollection ? 'Cada posición corresponde a una Print física exacta. El total solo suma versiones con valoración conservadora Cardmarket verificable.' : 'Sigue la Print física exacta que quieres, define tu objetivo y compáralo únicamente con su precio Cardmarket actual cuando la moneda coincida.'}</p>
            <div className="ux-library-actions">
              <Link href="/explorer" className="dri-btn dri-btn-primary">Buscar cartas</Link>
              <Link href={isCollection ? '/wishlist' : '/collection'} className="dri-btn dri-btn-ghost">{isCollection ? 'Ver wishlist' : 'Ver colección'}</Link>
            </div>
          </div>

          <div className={`library-summary v10-library-summary ${isCollection ? 'is-collection' : 'is-wishlist'}`}>
            <div className="library-stat"><span>{isCollection ? 'Prints distintas' : 'Prints seguidas'}</span><strong>{totalVersions.toLocaleString('es-ES')}</strong></div>
            {isCollection ? <div className="library-stat"><span>Cartas físicas</span><strong>{pieces.toLocaleString('es-ES')}</strong></div> : null}
            {isCollection ? <div className="library-stat v10-value-stat"><span>Valor conservador</span><strong>{money(data.known_value_eur || 0, 'EUR')}</strong></div> : null}
            {isCollection ? <div className="library-stat"><span>Cobertura valoración</span><strong>{coveragePercent}%</strong><small>{coverageCount} / {totalVersions}</small></div> : null}
            {!isCollection ? <div className="library-stat"><span>Con precio actual</span><strong>{wishlistStats.priced.toLocaleString('es-ES')}</strong></div> : null}
            {!isCollection ? <div className="library-stat"><span>Con objetivo</span><strong>{wishlistStats.targeted.toLocaleString('es-ES')}</strong></div> : null}
            {!isCollection ? <div className="library-stat v11-reached-stat"><span>Objetivo alcanzado</span><strong>{wishlistStats.reached.toLocaleString('es-ES')}</strong></div> : null}
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
        ) : (
          <section className="v11-wishlist-rule" aria-label="Regla de comparación de wishlist">
            <strong>Comparación exacta, sin FX implícito.</strong>
            <span>Solo marcamos un objetivo como alcanzado si esta Print tiene precio Cardmarket actual y usa la misma moneda que tu objetivo.</span>
          </section>
        )}

        {!loading && data.items?.length ? (
          <div className="v10-library-toolbar">
            <label>
              <span className="v10-sr-only">{isCollection ? 'Filtrar colección' : 'Filtrar wishlist'}</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Carta, set, número, idioma o Print ID…" />
            </label>
            <select value={sort} onChange={(event) => setSort(event.target.value)} aria-label={isCollection ? 'Ordenar colección' : 'Ordenar wishlist'}>
              <option value="recent">Más recientes</option>
              <option value="name">Nombre A–Z</option>
              {isCollection ? <option value="value_desc">Valor conservador ↓</option> : null}
              {isCollection ? <option value="quantity_desc">Cantidad ↓</option> : null}
              {!isCollection ? <option value="priority_desc">Prioridad ↓</option> : null}
              {!isCollection ? <option value="target_status">Objetivo / cercanía</option> : null}
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
            <h2>No encontramos esa Print en tu {isCollection ? 'colección' : 'wishlist'}.</h2>
            <p>Prueba otro nombre, set, idioma o número de coleccionista.</p>
            <button type="button" className="dri-btn dri-btn-ghost" onClick={() => setQuery('')}>Limpiar búsqueda</button>
          </div>
        ) : null}

        {!loading && visibleItems.length ? (
          <div className={`library-grid v10-library-grid ${!isCollection ? 'v11-wishlist-grid' : ''}`}>
            {visibleItems.map((item) => (
              <LibraryCard
                key={item.id}
                item={item}
                kind={kind}
                onRemove={remove}
                onQuantity={changeQuantity}
                onWishlistUpdate={updateWishlist}
              />
            ))}
          </div>
        ) : null}
      </section>
    </main>
  )
}

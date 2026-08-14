'use client'

import Link from 'next/link'
import FallbackImage from '../common/FallbackImage'
import SearchV2Results from './SearchV2Results'
import { getSetHref } from '../../lib/catalog/routes'
import './FederatedSearchResults.css'

function money(value, currency = 'EUR') {
  if (value === null || value === undefined) return null
  try {
    return new Intl.NumberFormat('es-ES', {
      style: 'currency',
      currency,
      maximumFractionDigits: 2,
    }).format(Number(value))
  } catch {
    return `${value} ${currency}`
  }
}

function safeCount(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue
    const numeric = Number(value)
    if (Number.isFinite(numeric)) return Math.max(0, numeric)
  }
  return 0
}

function SetResults({ items = [], gameSlug, total = null }) {
  const displayTotal = safeCount(total, items.length)
  return (
    <section className="fsr-section">
      <div className="fsr-section-head">
        <div>
          <p className="eyebrow">Colecciones</p>
          <h2>Sets que coinciden</h2>
        </div>
        <span>{displayTotal}</span>
      </div>
      {!items.length ? (
        <div className="dri-soft-empty">
          <strong>No encontramos colecciones para esta búsqueda.</strong>
          <p>Prueba con el código del set o con una parte de su nombre.</p>
        </div>
      ) : (
        <div className="fsr-set-grid">
          {items.map((item) => (
            <Link key={item.id || item.code} href={getSetHref(gameSlug, item.code)} className="fsr-set-card panel-soft">
              <span className="fsr-set-code">{String(item.code || '').toUpperCase()}</span>
              <div>
                <h3>{item.name || item.code}</h3>
                <p>{Number(item.card_count || 0)} cartas · abrir checklist completo</p>
              </div>
              <strong>Ver colección →</strong>
            </Link>
          ))}
        </div>
      )}
    </section>
  )
}

function SealedResults({ payload = {}, gameSlug, category, onCategoryChange }) {
  const items = payload.items || []
  const categories = payload.categories || []
  const total = safeCount(payload.total, items.length)

  return (
    <section className="fsr-section">
      <div className="fsr-section-head">
        <div>
          <p className="eyebrow">Sellado</p>
          <h2>Productos relacionados</h2>
        </div>
        <span>{total}</span>
      </div>

      {categories.length ? (
        <label className="fsr-category-filter">
          <span>Tipo de producto</span>
          <select value={category} onChange={(event) => onCategoryChange(event.target.value)}>
            <option value="">Todos</option>
            {categories.map((item) => (
              <option key={item.value} value={item.value}>{item.value} ({item.count})</option>
            ))}
          </select>
        </label>
      ) : null}

      {!items.length ? (
        <div className="dri-soft-empty">
          <strong>No encontramos productos sellados relacionados.</strong>
          <p>Solo mostramos sellado que podemos relacionar con seguridad con esta búsqueda.</p>
        </div>
      ) : (
        <div className="fsr-sealed-grid">
          {items.map((item) => {
            const price = item.price || {}
            const priceLabel = money(price.conservative ?? price.value ?? price.trend ?? price.minimum, price.currency || 'EUR')
            const detailsHref = item.canonical_product_id ? `/products/${item.canonical_product_id}` : null
            return (
              <article key={`${item.external_product_id || item.id}-${item.external_id || ''}`} className="fsr-sealed-card panel-soft">
                <div className="fsr-sealed-media">
                  <FallbackImage
                    src={item.primary_image_url}
                    alt={item.name || 'Producto sellado'}
                    placeholderClassName="image-fallback"
                    label={String(item.category || gameSlug || 'TCG')}
                  />
                </div>
                <div className="fsr-sealed-copy">
                  <div className="fsr-sealed-meta">
                    <span>{item.category || item.product_type || 'Sellado'}</span>
                    {item.set_code ? <span>{String(item.set_code).toUpperCase()}</span> : null}
                  </div>
                  <h3>{item.name}</h3>
                  <div className="fsr-market-line">
                    <strong>{priceLabel || 'Sin Price Guide actual'}</strong>
                    <span>Cardmarket</span>
                  </div>
                  <div className="fsr-actions">
                    {item.cardmarket?.url ? (
                      <a href={item.cardmarket.url} target="_blank" rel="noopener noreferrer sponsored" className="dri-btn">
                        Comprar en Cardmarket ↗
                      </a>
                    ) : null}
                    {detailsHref ? <Link href={detailsHref} className="dri-btn dri-btn-ghost">Ver producto</Link> : null}
                  </div>
                </div>
              </article>
            )
          })}
        </div>
      )}
    </section>
  )
}

function Pagination({ page, total, pageSize, onPageChange }) {
  const totalPages = Math.max(1, Math.ceil(Number(total || 0) / pageSize))
  if (totalPages <= 1) return null
  return (
    <div className="fsr-pagination" aria-label="Paginación de resultados">
      <button type="button" className="dri-btn dri-btn-ghost" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>← Anterior</button>
      <span>Página {page} de {totalPages}</span>
      <button type="button" className="dri-btn dri-btn-ghost" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>Siguiente →</button>
    </div>
  )
}

export default function FederatedSearchResults({
  payload,
  gameSlug,
  query,
  activeType = 'all',
  onTypeChange,
  page = 1,
  onPageChange,
  pageSize = 24,
  category = '',
  onCategoryChange,
}) {
  if (!payload) return null
  const counts = payload.counts || {}
  const sets = payload.sets || []
  const singles = payload.singles || { items: [], total: 0 }
  const sealed = payload.sealed || { items: [], total: 0, categories: [] }
  const matches = payload.matches || []
  const setsPage = payload.sets_page || { total: safeCount(counts.sets, sets.length) }
  const isFirstPage = page === 1

  const singlesCount = safeCount(counts.singles, singles.total, singles.items?.length)
  const setsCount = safeCount(counts.sets, setsPage.total, sets.length)
  const sealedCount = safeCount(counts.sealed, sealed.total, sealed.items?.length)
  const matchesCount = safeCount(counts.matches, matches.length)
  const allCount = singlesCount + setsCount + sealedCount

  const tabs = [
    ['all', 'Todo', allCount],
    ['singles', 'Cartas', singlesCount],
    ['sets', 'Colecciones', setsCount],
    ['sealed', 'Sellado', sealedCount],
    ['matches', 'Coincidencias', matchesCount],
  ]

  const showSets = activeType === 'sets' || (activeType === 'all' && isFirstPage)
  const showSingles = activeType === 'all' || activeType === 'singles'
  const showSealed = activeType === 'sealed' || (activeType === 'all' && isFirstPage)
  const showMatches = activeType === 'matches' || (activeType === 'all' && isFirstPage)

  let paginationTotal = 0
  if (activeType === 'singles') paginationTotal = singles.total
  else if (activeType === 'sets') paginationTotal = setsPage.total
  else if (activeType === 'sealed') paginationTotal = sealed.total
  else if (activeType === 'all') paginationTotal = Number(singles.total || 0)

  return (
    <div className="fsr-shell">
      {payload.set_intent?.set_code ? (
        <div className="fsr-intent panel-soft">
          <span className="fsr-intent-badge">Búsqueda de colección</span>
          <div>
            <strong>{String(payload.set_intent.set_code).toUpperCase()}</strong>
            <p>Mostramos checklist físico, productos sellados y coincidencias relacionadas sin mezclar identidades.</p>
          </div>
        </div>
      ) : null}

      <div className="fsr-tabs" role="tablist" aria-label="Tipos de resultado">
        {tabs.map(([value, label, count]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={activeType === value}
            className={`fsr-tab ${activeType === value ? 'is-active' : ''}`}
            onClick={() => onTypeChange(value)}
          >
            {label} <span>{count}</span>
          </button>
        ))}
      </div>

      {showSets ? <SetResults items={sets} gameSlug={gameSlug} total={setsPage.total} /> : null}

      {showSingles ? (
        <SearchV2Results
          items={singles.items || []}
          mode="advanced"
          gameSlug={gameSlug}
          query={query}
          total={singles.total || 0}
        />
      ) : null}

      {showSealed ? (
        <SealedResults
          payload={sealed}
          gameSlug={gameSlug}
          category={category}
          onCategoryChange={onCategoryChange}
        />
      ) : null}

      {showMatches && matches.length ? (
        <section className="fsr-section fsr-related">
          <SearchV2Results items={matches} mode="normal" gameSlug={gameSlug} query={query} total={matches.length} />
        </section>
      ) : null}

      {activeType === 'matches' && !matches.length ? (
        <div className="dri-soft-empty fsr-tab-empty">
          <strong>No hay coincidencias adicionales.</strong>
          <p>Las cartas, colecciones y productos exactos siguen disponibles en sus pestañas.</p>
        </div>
      ) : null}

      <Pagination page={page} total={paginationTotal} pageSize={pageSize} onPageChange={onPageChange} />
    </div>
  )
}

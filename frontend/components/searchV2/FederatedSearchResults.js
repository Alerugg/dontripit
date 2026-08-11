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

function SetResults({ items = [], gameSlug }) {
  if (!items.length) return null
  return (
    <section className="fsr-section">
      <div className="fsr-section-head">
        <div>
          <p className="eyebrow">Colección</p>
          <h2>Sets que coinciden</h2>
        </div>
        <span>{items.length}</span>
      </div>
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
    </section>
  )
}

function SealedResults({ payload = {}, gameSlug, category, onCategoryChange }) {
  const items = payload.items || []
  const categories = payload.categories || []
  const total = Number(payload.total || 0)

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
          <strong>No hay sellado exacto en esta página.</strong>
          <p>Solo enseñamos productos que podemos relacionar con seguridad con esta búsqueda.</p>
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
  const isFirstPage = page === 1

  const tabs = [
    ['all', 'Todo', Number(counts.singles || 0) + Number(counts.sets || 0) + Number(counts.sealed || 0)],
    ['singles', 'Singles', Number(counts.singles || 0)],
    ['sets', 'Colección', Number(counts.sets || 0)],
    ['sealed', 'Sellado', Number(counts.sealed || 0)],
    ['matches', 'Coincidencias', Number(counts.matches || 0)],
  ]

  const showSets = activeType === 'all' || activeType === 'sets'
  const showSingles = activeType === 'all' || activeType === 'singles'
  const showSealed = activeType === 'all' || activeType === 'sealed'
  const showMatches = activeType === 'matches' || (activeType === 'all' && isFirstPage)

  let paginationTotal = 0
  if (activeType === 'singles') paginationTotal = singles.total
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
            className={`fsr-tab ${activeType === value ? 'is-active' : ''}`}
            onClick={() => onTypeChange(value)}
          >
            {label} <span>{count}</span>
          </button>
        ))}
      </div>

      {showSets && isFirstPage ? <SetResults items={sets} gameSlug={gameSlug} /> : null}

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

      <Pagination page={page} total={paginationTotal} pageSize={pageSize} onPageChange={onPageChange} />
    </div>
  )
}

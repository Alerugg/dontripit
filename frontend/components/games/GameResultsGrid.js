'use client'

import './GameResultsGrid.css'
import CatalogCard from '../catalog/CatalogCard'

export default function GameResultsGrid({ items = [], view = 'grid', queryState = {} }) {
  const resultCount = items.length
  const isSingles = queryState?.type === 'singles'

  return (
    <section className="game-results-block">
      <div className="game-results-head">
        <div>
          <p className="eyebrow">Resultados</p>
          <h2>{isSingles ? 'Cartas' : 'Resultados'}</h2>
        </div>
        <p className="game-results-copy">
          {resultCount} resultado{resultCount === 1 ? '' : 's'} en la vista actual.
        </p>
      </div>

      <div className={`game-results-grid is-${view} ${resultCount <= 3 ? 'is-few' : ''}`}>
        {items.map((item, index) => (
          <CatalogCard
            key={
              item?.id ??
              item?.print_id ??
              item?.card_id ??
              `${item?.name || item?.title || 'item'}-${item?.set_code || 'set'}-${item?.collector_number || index}`
            }
            item={item}
            view={view}
            queryState={queryState}
          />
        ))}
      </div>
    </section>
  )
}
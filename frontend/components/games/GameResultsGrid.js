'use client'

import './GameResultsGrid.css'
import CatalogCard from '../catalog/CatalogCard'

const RESULTS_COPY = {
  singles: {
    title: 'Prints',
    helper: 'Abre una carta para revisar variantes y su set en contexto.',
  },
  sealed: {
    title: 'Sellado y sets',
    helper: 'Resultados combinados para llegar rápido a producto y colecciones.',
  },
  all: {
    title: 'Resultados',
    helper: 'Incluye cartas, sets y producto según la coincidencia.',
  },
}

export default function GameResultsGrid({ items = [], view = 'grid', queryState = {} }) {
  const resultCount = items.length
  const selectedCopy = RESULTS_COPY[queryState?.type] || RESULTS_COPY.all

  return (
    <section className="game-results-block">
      <div className="game-results-head">
        <div>
          <p className="eyebrow">Resultados</p>
          <h2>{selectedCopy.title}</h2>
        </div>
        <div className="game-results-meta">
          <p className="game-results-copy">
            {resultCount} resultado{resultCount === 1 ? '' : 's'} en la vista actual.
          </p>
          <small>{selectedCopy.helper}</small>
        </div>
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

import ResultsGrid from '../catalog/ResultsGrid'

export default function GameResultsGrid({ items, view, queryState }) {
  return (
    <section className="game-section">
      <div className="section-heading compact">
        <p className="eyebrow">Resultados</p>
        <h2>Cartas</h2>
      </div>
      <ResultsGrid items={items} view={view} queryState={queryState} />
    </section>
  )
}

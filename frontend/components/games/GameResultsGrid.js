import ResultsGrid from '../catalog/ResultsGrid'

export default function GameResultsGrid({ items, view, queryState }) {
  return (
    <section className="game-section">
      <div className="section-heading compact">
        <p className="eyebrow">Resultados</p>
        <h2>Cartas maestras</h2>
        <p>La lista evita duplicados por variante y reserva el detalle completo para la ficha de la carta.</p>
      </div>
      <ResultsGrid items={items} view={view} queryState={queryState} />
    </section>
  )
}
